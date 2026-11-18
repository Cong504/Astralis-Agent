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
import time
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
        argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, check=False,
    )


def _parse_size_mb(value: str) -> int:
    """解析 docker stats 的 MemUsage 形如 '12.3MiB / 2GiB'，取已用部分转 MB。"""
    used = value.split("/")[0].strip()
    multipliers = {"KiB": 1 / 1024, "MiB": 1, "GiB": 1024, "B": 1 / 1048576}
    for unit, factor in multipliers.items():
        if used.endswith(unit):
            try:
                return int(float(used[:-len(unit)].strip()) * factor)
            except ValueError:
                return 0
    return 0


def _parse_block_io_written_mb(value: str) -> int:
    """解析 BlockIO 形如 '1.2MB / 0B'，取写入部分（斜杠右侧）转 MB。"""
    written = value.split("/")[1].strip() if "/" in value else "0B"
    multipliers = {"kB": 1 / 1000, "MB": 1, "GB": 1000, "B": 1 / 1000000}
    for unit, factor in multipliers.items():
        if written.endswith(unit):
            try:
                return int(float(written[:-len(unit)].strip()) * factor)
            except ValueError:
                return 0
    return 0


class DockerSandboxExecutor(SandboxExecutor):
    def __init__(self, docker_bin: str = "docker", runner: Runner | None = None):
        self.docker_bin = docker_bin
        self._run_cmd = runner or _default_runner
        # 冷启动计时（2.8 测量协议）：create 发出 → 首条命令 stdout 返回。
        # 每次 execute 覆盖；mock runner 下无意义。
        self.last_timing: dict[str, float] = {}
        self._create_issued_at: float | None = None

    # --- §3/§4 规则匹配（W2 真实实现，不再留桩） ---
    def check_command(self, request: ExecutionRequest) -> tuple[Verdict, str]:
        # 路径穿越（§5）优先匹配——它比具体系统路径（§3.4）更根本，归因也更准确
        for rule in FORBIDDEN_RULES:
            if rule.rule_id == "§5" and re.search(rule.pattern, request.command, re.IGNORECASE):
                return Verdict.DENIED_BY_RULE, rule.rule_id
        # 其余命令 + 路径规则（路径均出现在命令中，如 /proc/1/environ）
        for rule in FORBIDDEN_RULES:
            if rule.scope is not RuleScope.DENY or rule.rule_id == "§5":
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
        self._create_issued_at = time.perf_counter()  # 协议起点：create 发出
        argv = [self.docker_bin, "create"]
        argv += request.limits.docker_args()  # 已含 --network none
        # W2.5 白名单：仅当显式配置 dns_allowlist 时覆盖网络模式（默认 none 不重复加）
        if request.mounts.dns_allowlist:
            argv += ["--network", request.mounts.network_mode]
        for src, dst, mode in request.mounts.docker_mounts():
            argv += ["-v", f"{src}:{dst}:{mode}"]
        # tmpfs 默认属主 root，须显式给 uid/gid 才能被非 root 用户写入（真实镜像验收发现）
        argv += ["--tmpfs",
                 f"{request.mounts.scratch_dst}:rw,size={request.mounts.scratch_size_mb}m,"
                 f"uid={request.limits.non_root_uid},gid={request.limits.non_root_uid}"]
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
        # 协议终点：首条命令 stdout 返回（不含其后的 stats 采集与容器销毁）
        if self._create_issued_at is not None:
            self.last_timing["create_to_stdout_sec"] = time.perf_counter() - self._create_issued_at
        # stats 在容器仍存在时采集（destroy 之前），峰值用于审计
        peak_mem, disk_written = self._collect_stats(container_id)
        exit_code = int((wait.stdout.strip() or "-1").splitlines()[-1])
        verdict = Verdict.OOM_KILLED if exit_code == 137 else Verdict.ALLOWED
        return ExecutionResult(
            task_id=request.task_id, step_id=request.step_id, timestamp=utc_now(),
            command=request.command, verdict=verdict, container_id=container_id,
            exit_code=exit_code, stdout=logs.stdout, stderr=logs.stderr,
            peak_memory_mb=peak_mem, disk_written_mb=disk_written,
            user_confirmed=request.user_confirmed,
        )

    def _collect_stats(self, container_id: str) -> tuple[int, int]:
        """W2.3：docker stats --no-stream 采集内存峰值与磁盘写入量。"""
        proc = self._run_cmd(
            [self.docker_bin, "stats", "--no-stream", "--format",
             "{{.MemUsage}}\t{{.BlockIO}}", container_id],
            None,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return 0, 0
        parts = proc.stdout.strip().splitlines()[0].split("\t")
        mem_mb = _parse_size_mb(parts[0]) if len(parts) > 0 else 0
        disk_mb = _parse_block_io_written_mb(parts[1]) if len(parts) > 1 else 0
        return mem_mb, disk_mb

    def collect(self, container_id: str) -> ExecutionResult:
        """接口保留：采集资源峰值。实际采集已在 run() 中于销毁前完成。"""
        raise NotImplementedError("资源采集已并入 run() 的 _collect_stats；本方法保留接口兼容")

    def destroy(self, container_id: str) -> None:
        self._run_cmd([self.docker_bin, "rm", "-f", container_id], None)
