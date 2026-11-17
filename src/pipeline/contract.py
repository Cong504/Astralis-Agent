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


class ApproveStage(PipelineStage):
    """W1：确认动作由 CLI 层注入；此段读取注入结果，不自行交互。"""
    name = "approve"

    def __init__(self, approved: bool = True):
        self.approved = approved

    def run(self, ctx: StepContext) -> StageResult:
        ctx.approve = {"approved": self.approved}
        return StageResult(self.approved, self.name, ctx.approve)


class ExecuteStage(PipelineStage):
    """W1 桩：只打印本应执行的工具调用，不真跑。W2 填入沙箱执行器。"""
    name = "execute"

    def run(self, ctx: StepContext) -> StageResult:
        intent = {"tool": ctx.step.get("tool"), "args": ctx.step.get("args", {}), "stub": True}
        ctx.execute = intent
        return StageResult(True, self.name, intent)


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
