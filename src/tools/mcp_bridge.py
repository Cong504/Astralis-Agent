"""MCP 工具桥接规范（W2 预备，仅契约与规范，不含真实工具实现）。

上游依据：DEVELOPMENT_PLAN.md W2（2.6 五个基础工具、2.7 全链路）、
ARCHITECTURE.md §2（Tools 边界）、PACKAGE_SPEC.md §6（tools.json 权限声明）、
SANDBOX_RULES.md §4/§5/§6、W1-plan.md §3（A3 假设：MCP Stdio 承载工具接入）。

边界（ARCHITECTURE.md §1/§2）：
  - Tools 只接受 Runtime 的工具调用指令，返回执行结果；不直接写记忆库。
  - 工具不直接执行副作用：所有命令/脚本必须交给 SandboxExecutor（src/sandbox/executor.py）。
  - 工具生成的能力受白名单约束；高危由执行层的 FORBIDDEN_RULES 拦截，不依赖提示词。

A3 假设（W1-plan.md §3）：先用 MCP Stdio echo 打通「运行时 ↔ 工具进程」一次调用握手。
若 Stdio 集成不顺，W2.6 回退自定义插件接口并出 ADR（W1-plan.md §6 预案）。

规范要点：
  1. 工具注册表：5 个基础工具，名称与 plan.schema.json 的 tool 枚举一致。
  2. 调用协议：Runtime 传入 ToolCall（tool + args + 风险上下文），bridge 返回 ToolResult。
  3. 权限声明：对齐 PACKAGE_SPEC.md §6 的「声明 ∩ 本地策略」取交集，不可放宽。
  4. 确认机制：risk_level=high 或命中 SANDBOX_RULES.md §4 的工具调用须带 confirmed=True。

注意：本文件是规范定义。W2.6 实现 Bridge 时必须实现 MCPBridge 抽象基类的全部方法，
且不得在 tools 层直接启动 Docker 或读写数据层。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ToolRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class ToolSpec:
    """单个工具的规范定义（对应 PACKAGE_SPEC.md tools.json 中一项 + skills yaml 字段）。"""
    name: str
    description: str
    inputs: tuple[str, ...]                 # 参数名列表，须与 plan.schema.json args 对齐
    risk_level: ToolRisk
    requires_confirm: bool = False
    needs_sandbox: bool = True              # 是否必须经 SandboxExecutor 执行
    filesystem_read: tuple[str, ...] = ()   # PACKAGE_SPEC.md §6 权限声明：允许读路径
    filesystem_write: tuple[str, ...] = ()  # 允许写路径（须在用户确认后授予）
    network: bool = False                   # v0.1 恒为 False（§6 默认断网）

    def __post_init__(self) -> None:
        # PACKAGE_SPEC.md §5：risk_level=high ⇒ requires_confirm 必须为 true
        if self.risk_level is ToolRisk.HIGH and not self.requires_confirm:
            raise ValueError(f"工具 {self.name}: high 风险必须 requires_confirm=true")


# W2.6 的 5 个基础工具（名称与 plan.schema.json tool 枚举严格一致）
BUILTIN_TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="dir_scan",
        description="扫描目录并统计文件类型分布（S2 场景主工具）",
        inputs=("path",),
        risk_level=ToolRisk.LOW,
        filesystem_read=("/workspace",),
    ),
    ToolSpec(
        name="file_read",
        description="读取指定文本文件并返回内容",
        inputs=("path",),
        risk_level=ToolRisk.LOW,
        filesystem_read=("/workspace",),
    ),
    ToolSpec(
        name="script_run",
        description="在沙箱内执行脚本/命令，须过执行层禁止清单",
        inputs=("script",),
        risk_level=ToolRisk.MEDIUM,
        filesystem_read=("/workspace",),
        filesystem_write=("/tmp/agent-scratch",),
    ),
    ToolSpec(
        name="report_write",
        description="向可写目录生成结构化报告（§4 文件写入需确认）",
        inputs=("target", "content"),
        risk_level=ToolRisk.MEDIUM,
        requires_confirm=True,
        filesystem_write=("/tmp/agent-scratch",),
    ),
    ToolSpec(
        name="log_record",
        description="记录一条审计事件到数据层（由 Runtime 调用，不直接执行副作用）",
        inputs=("task_id", "step_id", "event"),
        risk_level=ToolRisk.LOW,
        needs_sandbox=False,
    ),
)

TOOL_REGISTRY: dict[str, ToolSpec] = {t.name: t for t in BUILTIN_TOOLS}


@dataclass
class ToolCall:
    """Runtime 下发的工具调用指令。"""
    task_id: str
    step_id: str
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    confirmed: bool = False                 # §4 操作的二次确认结果


@dataclass
class ToolResult:
    """工具执行结果。"""
    tool: str
    ok: bool
    output: dict[str, Any] = field(default_factory=dict)
    verdict: str = ""                       # 来自 ExecutionResult.verdict
    matched_rule: str = ""
    error: str = ""


class MCPBridge(ABC):
    """MCP 工具桥接抽象基类（接口，W2.6 实现）。

    实现约束：
      - 所有 needs_sandbox=True 的工具必须把调用转成 ExecutionRequest 交给 SandboxExecutor，
        不得在工具进程内直接执行命令（SANDBOX_RULES.md §1.1「禁止本地直跑」）。
      - 权限取交集：调用路径若超出 ToolSpec 声明或本地策略，须拒绝并记 DENIED。
      - 不得在 tools 层发起模型 API 调用或持有 Key（§6）。
    """

    @abstractmethod
    def list_tools(self) -> list[ToolSpec]:
        """返回已注册工具列表（供 Runtime 展示与 Package 导出使用）。"""
        ...

    @abstractmethod
    def call(self, invocation: ToolCall) -> ToolResult:
        """执行一个工具调用；内部完成权限校验、确认校验、沙箱派发与结果回收。"""
        ...

    @abstractmethod
    def shutdown(self) -> None:
        """关闭工具进程（Stdio 实现须终止子进程），不残留后台进程。"""
        ...


class StdioMCPBridge(MCPBridge):
    """MCP Stdio 传输占位（A3 验证用）。

    W2 实现要点（A3 假设）：
      - 以子进程启动 MCP server，通过 stdin/stdout 交换 JSON-RPC 消息；
      - 一次握手：initialize → tools/list → tools/call，验证进程间调用打通即可，
        不要求 5 个工具全实现（那是 2.6）；
      - 若 Stdio 集成阻塞 >1 天，按 W1-plan.md §6 回退自定义插件接口并出 ADR。
    """

    def __init__(self, server_cmd: list[str]):
        self.server_cmd = server_cmd

    def list_tools(self) -> list[ToolSpec]:
        raise NotImplementedError("A3 / W2.6 实现：Stdio tools/list 握手")

    def call(self, invocation: ToolCall) -> ToolResult:
        raise NotImplementedError("A3 / W2.6 实现：Stdio tools/call 往返")

    def shutdown(self) -> None:
        raise NotImplementedError("A3 / W2.6 实现：终止 Stdio 子进程")


def validate_call(invocation: ToolCall, local_network_enabled: bool = False) -> str:
    """调用前置校验：返回拒绝原因，空串表示通过。不执行任何副作用。

    校验顺序对齐 PACKAGE_SPEC.md §6「声明 ∩ 本地策略」与 SANDBOX_RULES.md §4：
      1. 工具名必须在白名单
      2. high 风险 / §4 操作必须 confirmed
      3. 网络类工具在本地不允许时拒绝
    """
    spec = TOOL_REGISTRY.get(invocation.tool)
    if spec is None:
        return f"未知工具: {invocation.tool}"
    if spec.requires_confirm and not invocation.confirmed:
        return f"工具 {spec.name} 需用户二次确认（SANDBOX_RULES.md §4）"
    if spec.network and not local_network_enabled:
        return f"工具 {spec.name} 需要网络，但本地策略默认断网（SANDBOX_RULES.md §6）"
    return ""
