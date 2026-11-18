"""Docker CLI 沙箱执行器（W2.2/W2.3/W2.4 骨架，基于 docker 命令）。

实现 `executor.py` 的 SandboxExecutor 接口，只填空、不改签名（ADR-0001）。
不引入 docker SDK 依赖——用 subprocess 调 docker CLI（E06 已验证可用）。

安全落点：
  - check_command() 真正调用 §3/§4 规则匹配，包管理操作 DENIED_BY_RULE §3.5（人类硬约束）。
  - create_container 用 ResourceLimits.docker_args() + MountPolicy.docker_mounts()，不复用容器。
  - run() 带 step_timeout_s 看门狗，超时 docker kill + TIMEOUT。
  - execute() 模板方法（基类）保证 try/finally 销毁容器。
"""

from __future__ import annotations

import re
import subprocess
from typing import Callable

from data.events import utc_now
from sandbox.executor import (
    CONFIRM_RULES, FORBIDDEN_RULES, ExecutionRequest, ExecutionResult,
    RuleScope, SandboxExecutor, Verdict,
)

# runner 类型：接受 argv 与 timeout，返回 CompletedProcess。测试可注入 mock。
Runner = Callable[[list[str], float | None], subprocess.CompletedProcess]


def _default_runner(argv: list[str], timeout: float | None) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv, capture_output=True, text=True, timeout=timeout, check=False,
    )


class DockerSandboxExecutor(SandboxExecutor):
    def __init__(self, docker_bin: str = "docker", runner: Runner | None = None):
        self.docker_bin = docker_bin
        self._run_cmd = runner or _default_runner

    # --- §3/§4 规则匹配（W2 真实实现，不再留桩） ---
    def check_command(self, request: ExecutionRequest) -> tuple[Verdict, str]:
        # 命令文本 + 路径规则（路径均出现在命令中，如 ../../etc/passwd）
        for rule in FORBIDDEN_RULES:
            if rule.scope is not RuleScope.DENY:
                continue
            if rule.applies_to in ("command", "path"):
                if re.search(rule.pattern, request.command, re.IGNORECASE):
                    return Verdict.DENIED_BY_RULE, rule.rule_id
        for rule in CONFIRM_RULES:
            if re.search(rule.pattern, request.command, re.IGNORECASE):
                if not request.user_confirmed:
                    return Verdict.NEED_CONFIRM, rule.rule_id
        return Verdict.ALLOWED, ""

    # --- 容器生命周期 ---
    def create_container(self, request: ExecutionRequest) -> str:
        argv = [self.docker_bin, "create"]
        argv += request.limits.docker_args()
        argv += ["--network", request.mounts.network_mode]
        for src, dst, mode in request.mounts.docker_mounts():
            argv += ["-v", f"{src}:{dst}:{mode}"]
        argv += ["--tmpfs", f"{request.mounts.scratch_dst}:rw,size={request.mounts.scratch_size_mb}m"]
        argv += [request.image, "/bin/bash", "-lc", request.command]
        proc = self._run_cmd(argv, None)
        if proc.returncode != 0:
            raise RuntimeError(f"docker create 失败: {proc.stderr.strip()}")
        return proc.stdout.strip()

    def run(self, container_id: str, request: ExecutionRequest) -> ExecutionResult:
        # 启动
        start = self._run_cmd([self.docker_bin, "start", container_id], None)
        if start.returncode != 0:
            return ExecutionResult(
                task_id=request.task_id, step_id=request.step_id, timestamp=utc_now(),
                command=request.command, verdict=Verdict.DENIED_BY_RULE,
                container_id=container_id, exit_code=start.returncode,
                stderr=start.stderr,
            )
        # 等待（带看门狗）
        try:
            wait = self._run_cmd([self.docker_bin, "wait", container_id],
                                 float(request.limits.step_timeout_s))
        except subprocess.TimeoutExpired:
            self._run_cmd([self.docker_bin, "kill", container_id], None)
            return ExecutionResult(
                task_id=request.task_id, step_id=request.step_id, timestamp=utc_now(),
                command=request.command, verdict=Verdict.TIMEOUT,
                container_id=container_id, matched_rule="§2-timeout",
            )
        logs = self._run_cmd([self.docker_bin, "logs", container_id], None)
        exit_code = int((wait.stdout.strip() or "-1").splitlines()[-1])
        verdict = Verdict.OOM_KILLED if exit_code == 137 else Verdict.ALLOWED
        return ExecutionResult(
            task_id=request.task_id, step_id=request.step_id, timestamp=utc_now(),
            command=request.command, verdict=verdict, container_id=container_id,
            exit_code=exit_code, stdout=logs.stdout, stderr=logs.stderr,
            user_confirmed=request.user_confirmed,
        )

    def collect(self, container_id: str) -> ExecutionResult:
        # D1 骨架：资源峰值采集留 W2.3 接入 docker stats；此处只返回占位字段。
        raise NotImplementedError("W2.3 实现：docker stats 采集内存/磁盘峰值")

    def destroy(self, container_id: str) -> None:
        self._run_cmd([self.docker_bin, "rm", "-f", container_id], None)
