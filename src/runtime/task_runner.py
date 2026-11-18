"""Runtime 任务状态机（ARCHITECTURE.md §3）。

职责：唯一编排 Model / Tools / Sandbox / Data 的地方；持有 TokenBudget，
跨多次模型调用累计并在超限时中止任务 + 告警（ADR-0003）。
Interface 通过 RuntimeUI 回调注入展示/确认/进度反馈，Runtime 不直接依赖 CLI。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from data.db import TaskLogger
from data.events import EventLog
from model.client import generate_plan_with_retry
from model.router import ModelRouter, build_stub_router
from pipeline.contract import (
    ApproveStage, ExecuteStage, GuardStage, PostExecuteStage, PreExecuteStage, StepContext,
)
from runtime.token_budget import TokenBudget, TokenBudgetConfig, TokenBudgetExceeded


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass
class RuntimeUI:
    """Interface 层回调契约（Runtime 只调用，不实现展示细节）。"""
    show_plan: Any = lambda plan: None
    ask_confirm: Any = lambda: True
    progress_start: Any = lambda: None
    progress_stop: Any = lambda: None
    show_intent: Any = lambda intent: None
    alert: Any = lambda message: None
    info: Any = lambda message: None


PLANNING_PROMPT = (
    "你是任务规划器。只输出 JSON，字段为 task_id/goal/steps，"
    "steps 每项含 step_id/action/tool/args/risk_level，"
    "risk_level 取 low|medium|high，high 时必须有 requires_confirm=true。\n"
    "task_id: {task_id}\n任务: {raw_input}"
)


def build_pipeline(approved: bool):
    return [
        PreExecuteStage(),
        GuardStage(),
        ApproveStage(approved=approved),
        ExecuteStage(),
        PostExecuteStage(),
    ]


def run_task(
    raw_input: str,
    router: ModelRouter | None = None,
    db: TaskLogger | None = None,
    events: EventLog | None = None,
    ui: RuntimeUI | None = None,
    budget_config: TokenBudgetConfig | None = None,
    own_db: bool = False,
) -> str:
    """跑一条任务闭环，返回 task_id。

    own_db=True 时由本函数负责 close（CLI 调用用；测试注入 db 时为 False）。
    """
    router = router or build_stub_router()
    ui = ui or RuntimeUI()
    db = db or TaskLogger()
    events = events or EventLog()
    task_id = f"task-{uuid.uuid4().hex[:8]}"
    budget = TokenBudget(config=budget_config, task_id=task_id)

    try:
        events.append(task_id, "RECEIVED", {"raw_input": raw_input})
        db.create_task(task_id, raw_input, "RECEIVED", now())

        events.append(task_id, "PLANNING", {})
        db.update_status(task_id, "PLANNING", now())

        prompt = PLANNING_PROMPT.format(task_id=task_id, raw_input=raw_input)
        ui.progress_start()
        try:
            plan, route = generate_plan_with_retry(router, prompt)
        finally:
            ui.progress_stop()
        plan["task_id"] = task_id

        # Runtime 累计 usage（跨多次调用）；超限即中止任务并告警
        try:
            usage_snap = budget.consume(route.usage)
        except TokenBudgetExceeded as exc:
            events.append(task_id, "FAILED", {
                "reason": "token_budget_exceeded", "backend": route.backend,
                "snapshot": exc.snapshot,
            })
            db.update_status(task_id, "FAILED", now())
            ui.alert(f"任务已中止：{exc}（task_id={task_id}）")
            if not own_db:
                db.close()
            return task_id

        db.save_plan(task_id, plan["goal"], plan, now())
        db.log_action(
            task_id, "model_call", "OK", now(),
            tool=route.backend, result={"usage": usage_snap, "fallback": route.fallback},
        )
        ui.show_plan(plan)
        events.append(task_id, "AWAIT_CONFIRM", {"backend": route.backend, "fallback": route.fallback})
        db.update_status(task_id, "AWAIT_CONFIRM", now())

        if not ui.ask_confirm():
            events.append(task_id, "CANCELLED", {"reason": "user"})
            db.update_status(task_id, "CANCELLED", now())
            ui.info("已取消，日志已记录 CANCELLED。")
            if not own_db:
                db.close()
            return task_id

        events.append(task_id, "EXECUTING", {"backend": route.backend, "fallback": route.fallback})
        db.update_status(task_id, "EXECUTING", now())

        pipeline = build_pipeline(approved=True)
        for step in plan["steps"]:
            ctx = StepContext(task_id=task_id, step=step, goal=plan["goal"])
            results = []
            for stage in pipeline:
                result = stage.run(ctx)
                results.append({"stage": stage.name, "ok": result.ok, "detail": result.detail})
            ui.show_intent(ctx.execute)
            db.log_action(
                task_id, "execute_stub", "STUB", now(),
                step_id=step.get("step_id"), tool=step.get("tool"),
                args=step.get("args"), result={"pipeline": results},
            )

        events.append(task_id, "DONE", {"backend": route.backend, "usage": usage_snap})
        db.update_status(task_id, "DONE", now())
        ui.info(f"完成。task_id={task_id}")
        return task_id
    finally:
        if own_db:
            db.close()
