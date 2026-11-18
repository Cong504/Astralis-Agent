"""五段执行管道契约。

设计依据：W1-plan.md §1.6「pre_execute → guard → approve → execute → post_execute，
W1 阶段 guard 和 approve 用桩实现，但接口签名本周就要定死，W2/W3 再填真逻辑」。

依赖方向遵循 ARCHITECTURE.md §1：Runtime 编排管道，管道各段不直接访问 Data 层。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StepContext:
    task_id: str
    step: dict[str, Any]
    goal: str = ""
    pre_execute: dict[str, Any] = field(default_factory=dict)
    guard: dict[str, Any] = field(default_factory=dict)
    approve: dict[str, Any] = field(default_factory=dict)
    execute: dict[str, Any] = field(default_factory=dict)
    post_execute: dict[str, Any] = field(default_factory=dict)


@dataclass
class StageResult:
    ok: bool
    action: str
    detail: dict[str, Any] = field(default_factory=dict)


class PipelineStage(ABC):
    name: str = "base"

    @abstractmethod
    def run(self, ctx: StepContext) -> StageResult:
        ...


class PreExecuteStage(PipelineStage):
    name = "pre_execute"

    def run(self, ctx: StepContext) -> StageResult:
        ctx.pre_execute = {"prepared": True, "step_id": ctx.step.get("step_id")}
        return StageResult(True, self.name, ctx.pre_execute)


class GuardStage(PipelineStage):
    """W1 桩：直接放行。W3 填入 SANDBOX_RULES.md §3 高危命令拒绝层。"""
    name = "guard"

    def run(self, ctx: StepContext) -> StageResult:
        ctx.guard = {"decision": "ALLOWED", "stub": True}
        return StageResult(True, self.name, ctx.guard)


# SANDBOX_RULES.md §4：以下操作必须步骤级二次确认，不因任务级确认而自动放行。
_CONFIRM_TOOLS = {"report_write"}                       # 文件写入
_CONFIRM_ARGS = ("target", "path", "script", "command")  # 可能指向写/删/改的参数


def needs_step_confirm(step: dict[str, Any]) -> tuple[bool, str]:
    """判断一个步骤是否需要 §4 步骤级二次确认。返回 (是否需要, 原因)。

    规则（对齐 SANDBOX_RULES.md §4）：
      - tool 标 high 风险 → 必须确认（与 PACKAGE_SPEC §5 一致）
      - report_write（文件写入）→ 必须确认
      - script/command 含写删安装特征（> / mv / rm / install）→ 必须确认
      - args 显式 requires_confirm=True → 必须确认
    纯读（dir_scan/file_read）与 log_record 不需要。
    """
    tool = str(step.get("tool", ""))
    args = step.get("args", {}) or {}
    if step.get("risk_level") == "high" or args.get("requires_confirm") is True:
        return True, "high 风险或显式要求确认"
    if tool in _CONFIRM_TOOLS:
        return True, f"§4 文件写入类工具 {tool}"
    script = " ".join(str(args.get(k, "")) for k in ("script", "command"))
    if script:
        markers = (" > ", " >> ", "rm ", "mv ", "pip install", "apt ", "apt-get ", "npm install")
        if any(m in script for m in markers):
            return True, "§4 写/删/安装类命令"
    return False, ""


class ApproveStage(PipelineStage):
    """确认段：任务级确认（approved）+ §4 操作步骤级二次确认。

    接口签名保持兼容（ADR-0001）：构造仍接受 approved；step_confirmer 可选，
    未提供时 §4 操作一律 NEED_CONFIRM（安全默认，不放行）。
    """
    name = "approve"

    def __init__(self, approved: bool = True, step_confirmer: Any | None = None):
        self.approved = approved
        self.step_confirmer = step_confirmer

    def run(self, ctx: StepContext) -> StageResult:
        if not self.approved:
            ctx.approve = {"approved": False, "reason": "task_not_approved"}
            return StageResult(False, self.name, ctx.approve)
        need, reason = needs_step_confirm(ctx.step)
        if need:
            granted = bool(self.step_confirmer and self.step_confirmer(ctx.step, reason))
            ctx.approve = {"approved": granted, "step_confirm_required": True,
                           "reason": reason, "verdict": "NEED_CONFIRM" if not granted else "CONFIRMED"}
            return StageResult(granted, self.name, ctx.approve)
        ctx.approve = {"approved": True, "step_confirm_required": False}
        return StageResult(True, self.name, ctx.approve)


class ExecuteStage(PipelineStage):
    """W1 桩：只打印本应执行的工具调用，不真跑。W2 用 SandboxExecuteStage 替换。"""
    name = "execute"

    def run(self, ctx: StepContext) -> StageResult:
        intent = {"tool": ctx.step.get("tool"), "args": ctx.step.get("args", {}), "stub": True}
        ctx.execute = intent
        return StageResult(True, self.name, intent)


class SandboxExecuteStage(PipelineStage):
    """W2 真执行段：把 StepContext 转成 ToolCall，交给注入的 dispatcher（BuiltinTools.call）。

    五段管道接口签名不变（ADR-0001）——仅替换 execute 段实现。
    结果写回 ctx.execute，字段含 verdict / exit_code / stdout，供 Runtime 落库与数据流下游使用。
    """
    name = "execute"

    def __init__(self, dispatcher: Any, confirmed: bool = True):
        self.dispatcher = dispatcher
        self.confirmed = confirmed

    def run(self, ctx: StepContext) -> StageResult:
        from tools.mcp_bridge import ToolCall  # 延迟导入，避免包循环

        call = ToolCall(
            task_id=ctx.task_id,
            step_id=ctx.step.get("step_id", ""),
            tool=ctx.step.get("tool", ""),
            args=dict(ctx.step.get("args", {})),
            confirmed=self.confirmed,
        )
        result = self.dispatcher(call)  # ToolResult
        detail = {
            "tool": result.tool,
            "step_id": call.step_id,
            "stub": False,
            "ok": result.ok,
            "verdict": result.verdict,
            "matched_rule": result.matched_rule,
            "output": result.output,
            "error": result.error,
        }
        ctx.execute = detail
        return StageResult(result.ok, self.name, detail)


class PostExecuteStage(PipelineStage):
    name = "post_execute"

    def run(self, ctx: StepContext) -> StageResult:
        ctx.post_execute = {"recorded": True}
        return StageResult(True, self.name, ctx.post_execute)


DEFAULT_STAGES = (
    PreExecuteStage,
    GuardStage,
    ApproveStage,
    ExecuteStage,
    PostExecuteStage,
)
