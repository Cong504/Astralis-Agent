"""沙箱执行器接口定义（W2 预备，仅契约，不含真实 Docker 调用实现）。

上游依据：SANDBOX_RULES.md §2/§3/§4/§5/§6/§7、ARCHITECTURE.md §5/§8.2、
DEVELOPMENT_PLAN.md W2（2.1–2.4）。

边界（ARCHITECTURE.md §1）：Sandbox 只接收脚本/命令，返回 stdout/stderr + 退出码，
不感知业务语义、不持久化状态、不持有 API Key。

本文件定义：
  1. 资源限制与挂载的不可变配置（ResourceLimits / MountPolicy）
  2. 禁止命令清单的结构化表示（对齐 §3 的 5 组、§4 确认类）
  3. 执行请求/结果数据契约（对齐 §7 审计字段）
  4. SandboxExecutor 抽象基类：新建容器 → 执行 → 采集 → 销毁

W2 实现者必须遵守：
  - 每次执行新建容器，执行后立即销毁，不复用（§8.2）
  - 资源限制通过 docker run 参数强制，不依赖容器内自觉（§2）
  - 高危命令拦截在提交容器之前完成，命中即 DENIED，不执行、不询问（§3）
  - 判定为 TIMEOUT 时先终止容器再记录（§2 / §7）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Verdict(str, Enum):
    """SANDBOX_RULES.md §7 的拦截判定结果。"""
    ALLOWED = "ALLOWED"
    DENIED_BY_RULE = "DENIED_BY_RULE"
    NEED_CONFIRM = "NEED_CONFIRM"
    TIMEOUT = "TIMEOUT"
    OOM_KILLED = "OOM_KILLED"


@dataclass(frozen=True)
class ResourceLimits:
    """SANDBOX_RULES.md §2 默认约束。实现时必须逐一映射为 docker run 参数。

    cpu_cores      -> --cpus
    memory_mb      -> --memory
    disk_mb        -> --storage-opt size（或等价配额）
    pids_limit     -> --pids-limit
    step_timeout_s -> 执行器侧看门狗，超时 kill + 记 TIMEOUT
    non_root_uid   -> --user（禁止 root）
    privileged     -> 必须为 False；实现须拒绝 --privileged 与新增 capability
    """
    cpu_cores: float = 2.0
    memory_mb: int = 2048
    disk_mb: int = 500
    pids_limit: int = 10
    step_timeout_s: int = 60
    non_root_uid: int = 10001
    privileged: bool = False

    def docker_args(self) -> list[str]:
        """把限制转成 docker run 参数片段；W2 实现须原样使用，不得放宽。"""
        if self.privileged:
            raise ValueError("SANDBOX_RULES.md §2 禁止 privileged")
        return [
            "--cpus", str(self.cpu_cores),
            "--memory", f"{self.memory_mb}m",
            "--pids-limit", str(self.pids_limit),
            "--user", str(self.non_root_uid),
            "--network", "none",
            "--cap-drop", "ALL",
        ]


@dataclass(frozen=True)
class MountPolicy:
    """SANDBOX_RULES.md §5 文件系统策略 + §6 网络策略。"""
    workspace_src: str = ""            # 宿主任务输入目录；容器内只读挂到 /workspace
    workspace_dst: str = "/workspace"
    scratch_dst: str = "/tmp/agent-scratch"  # 可写，≤500MB，任务结束即销毁
    scratch_size_mb: int = 500
    output_src: str = ""               # 用户确认后才可写挂载，否则为空
    output_dst: str = "/output"
    network_mode: str = "none"         # §6 默认全断；白名单需显式配置（W2.5）
    dns_allowlist: tuple[str, ...] = ()

    def docker_mounts(self) -> list[tuple[str, str, str]]:
        """返回 (src, dst, mode) 列表；不挂载宿主 / /home / NAS 根目录。"""
        mounts: list[tuple[str, str, str]] = []
        if self.workspace_src:
            mounts.append((self.workspace_src, self.workspace_dst, "ro"))
        if self.output_src:
            mounts.append((self.output_src, self.output_dst, "rw"))
        # scratch 由实现用 tmpfs 创建，需在 W2.4 验证写只读路径必然失败
        return mounts


class RuleScope(str, Enum):
    DENY = "deny"            # §3：直接拒绝，不执行、不询问用户
    CONFIRM = "confirm"      # §4：需用户二次确认
    ALLOW = "allow"


@dataclass(frozen=True)
class Rule:
    """一条可执行层规则。pattern 为正则；命中即按 scope 处理。"""
    rule_id: str             # 如 "§3.1"
    scope: RuleScope
    pattern: str             # 正则字符串（大小写不敏感由匹配器保证）
    note: str = ""
    applies_to: str = "command"  # command | path | mount | network


# SANDBOX_RULES.md §3 禁止清单的结构化表示（逐条对齐，W3.1 直接消费）
FORBIDDEN_RULES: tuple[Rule, ...] = (
    # §3.1 破坏性命令
    Rule("§3.1", RuleScope.DENY, r"\brm\s+-rf\s+(/|/\*|~)"),
    Rule("§3.1", RuleScope.DENY, r"\bmkfs\."),
    Rule("§3.1", RuleScope.DENY, r"\bdd\s+if=.*of=/dev/"),
    Rule("§3.1", RuleScope.DENY, r":\(\)\s*\{.*:\|.*\}\s*;"),  # fork 炸弹
    Rule("§3.1", RuleScope.DENY, r"\bchmod\s+-R\s+777\s+/"),
    Rule("§3.1", RuleScope.DENY, r"\bchown\s+-R\s+.*\s+/"),
    # §3.2 系统控制
    Rule("§3.2", RuleScope.DENY, r"\b(shutdown|reboot|halt|poweroff)\b"),
    Rule("§3.2", RuleScope.DENY, r"\b(systemctl|service)\b"),
    Rule("§3.2", RuleScope.DENY, r"\b(insmod|rmmod|modprobe)\b"),
    # §3.3 网络外联
    Rule("§3.3", RuleScope.DENY, r"\b(curl|wget|nc|ncat|telnet|ssh|scp)\b"),
    Rule("§3.3", RuleScope.DENY, r"https?://", applies_to="network"),
    # §3.4 权限与逃逸
    Rule("§3.4", RuleScope.DENY, r"\b(sudo|su|doas)\b"),
    Rule("§3.4", RuleScope.DENY, r"\b(mount|umount|nsenter|unshare)\b"),
    Rule("§3.4", RuleScope.DENY, r"/var/run/docker\.sock", applies_to="path"),
    Rule("§3.4", RuleScope.DENY, r"^/(etc|boot|sys|dev)(/|$)", applies_to="path"),
    Rule("§3.4", RuleScope.DENY, r"^/proc/(?!self/)", applies_to="path"),
    # §3.5 包管理与安装
    Rule("§3.5", RuleScope.DENY, r"\b(apt|apt-get|yum|dnf|apk)\s"),
    Rule("§3.5", RuleScope.DENY, r"\bpip\s+install\b"),
    Rule("§3.5", RuleScope.DENY, r"\bnpm\s+install\s+-g\b"),
    # §5 路径穿越
    Rule("§5", RuleScope.DENY, r"\.\./", applies_to="path"),
)

# SANDBOX_RULES.md §4 需二次确认的操作（不自动放行）
CONFIRM_RULES: tuple[Rule, ...] = (
    Rule("§4", RuleScope.CONFIRM, r"(>|>>)\s*\S+", note="文件写入", applies_to="command"),
    Rule("§4", RuleScope.CONFIRM, r"\brm\b(?!\s+-rf\s+/)", note="文件删除", applies_to="command"),
    Rule("§4", RuleScope.CONFIRM, r"\b(mv|rename)\b", note="批量移动/重命名", applies_to="command"),
)


@dataclass
class ExecutionRequest:
    """提交给沙箱的一次执行请求。"""
    task_id: str
    step_id: str
    command: str                          # 原始命令/脚本内容（§7 要求完整留痕，不截断）
    image: str = "local-agent/sandbox:0.1"
    limits: ResourceLimits = field(default_factory=ResourceLimits)
    mounts: MountPolicy = field(default_factory=MountPolicy)
    user_confirmed: bool = False          # §4 操作为 True 才放行
    env: dict[str, str] = field(default_factory=dict)  # 禁止含任何 API Key（§6）


@dataclass
class ExecutionResult:
    """执行结果，字段严格对齐 SANDBOX_RULES.md §7 审计要求。"""
    task_id: str
    step_id: str
    timestamp: str
    command: str                          # 原始命令完整记录
    verdict: Verdict
    matched_rule: str = ""                # 命中规则编号，如 "§3.1"
    container_id: str = ""
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    peak_memory_mb: int = 0
    disk_written_mb: int = 0
    user_confirmed: bool = False
    confirm_at: str = ""

    def to_audit_record(self) -> dict[str, Any]:
        """转成可写日志的字典（由调用方写 SQLite / JSONL）。"""
        return {
            "task_id": self.task_id,
            "step_id": self.step_id,
            "timestamp": self.timestamp,
            "command": self.command,
            "verdict": self.verdict.value,
            "matched_rule": self.matched_rule,
            "container_id": self.container_id,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "peak_memory_mb": self.peak_memory_mb,
            "disk_written_mb": self.disk_written_mb,
            "user_confirmed": self.user_confirmed,
            "confirm_at": self.confirm_at,
        }


class SandboxExecutor:
    """沙箱执行器抽象基类（接口，不可直接实例化使用）。

    W2 实现（2.2/2.3/2.4）必须实现全部方法，并保证：
      - execute() 内部流程为 create → run → collect → destroy，容器不复用
      - 高危判定在 create_container 之前完成；DENIED 时不得创建容器
      - 任何路径返回前容器都已销毁（用 try/finally 保证）
    """

    def check_command(self, request: ExecutionRequest) -> tuple[Verdict, str]:
        """提交前的高危命令检查（§3/§4）。默认实现给出契约行为，W3 可扩展。

        默认只做接口示范：真实实现须按 FORBIDDEN_RULES / CONFIRM_RULES 正则匹配。
        返回 (verdict, matched_rule)。
        """
        raise NotImplementedError("W3.1 实现：按 FORBIDDEN_RULES/CONFIRM_RULES 匹配")

    def create_container(self, request: ExecutionRequest) -> str:
        """按 limits + mounts 新建一次性容器，返回 container_id。禁止复用。"""
        raise NotImplementedError("W2.2 实现")

    def run(self, container_id: str, request: ExecutionRequest) -> ExecutionResult:
        """在容器内执行，带 step_timeout_s 看门狗；超时返回 TIMEOUT 并终止容器。"""
        raise NotImplementedError("W2.2 / W2.3 实现")

    def collect(self, container_id: str) -> ExecutionResult:
        """采集 stdout/stderr/退出码/资源峰值。"""
        raise NotImplementedError("W2.2 实现")

    def destroy(self, container_id: str) -> None:
        """立即销毁容器；执行完必须调用，不得保留状态。"""
        raise NotImplementedError("W2.2 实现")

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        """模板方法：一次执行 = 一个容器 = 一次销毁（§8.2）。

        W2 实现可直接复用此方法，只需填充上述原子方法。
        """
        from data.events import utc_now  # 延迟导入，避免包循环
        verdict, rule = self.check_command(request)
        if verdict in (Verdict.DENIED_BY_RULE, Verdict.NEED_CONFIRM):
            # 两类都不建容器：DENIED 直接拒绝；NEED_CONFIRM 未确认不得放行。
            return ExecutionResult(
                task_id=request.task_id, step_id=request.step_id, timestamp=utc_now(),
                command=request.command, verdict=verdict, matched_rule=rule,
                user_confirmed=request.user_confirmed,
            )
        container_id = self.create_container(request)
        try:
            return self.run(container_id, request)
        finally:
            self.destroy(container_id)  # 任何路径都销毁，不复用
