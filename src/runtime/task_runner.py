"""Runtime 任务状态机（ARCHITECTURE.md §3）。

职责：唯一编排 Model / Tools / Sandbox / Data 的地方；持有 TokenBudget，
跨多次模型调用累计并在超限时中止任务 + 告警（ADR-0003）。
Interface 通过 RuntimeUI 回调注入展示/确认/进度反馈，Runtime 不直接依赖 CLI。

执行分两种模式（同一套五段管道，execute 段实现不同，ADR-0001）：
  - 默认（W1 stub）：ExecuteStage 只打印意图，不真跑。
  - 注入 executor+tools（W2）：用 SandboxExecuteStage 真跑，步骤输出可经
    `{{step-N.stdout}}` 引用给下游（S2 报告生成数据流）。
"""

from __future__ import annotations

import re
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
    ApproveStage, ExecuteStage, GuardStage, PipelineStage, PostExecuteStage,
    PreExecuteStage, SandboxExecuteStage, StepContext,
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

_STEP_REF = re.compile(r"\{\{\s*step-([0-9]+)\.stdout\s*\}\}")


def build_pipeline(approved: bool, dispatcher: Any | None = None):
    execute: PipelineStage = (
        SandboxExecuteStage(dispatcher, confirmed=approved) if dispatcher is not None
        else ExecuteStage()
    )
    return [PreExecuteStage(), GuardStage(), ApproveStage(approved=approved),
            execute, PostExecuteStage()]


def _render_args(args: dict[str, Any], outputs: dict[str, str]) -> dict[str, Any]:
    """把 args 中的 {{step-N.stdout}} 替换为前序步骤输出（S2 数据流）。"""
    rendered: dict[str, Any] = {}
    for key, value in args.items():
        if isinstance(value, str):
            def repl(m: re.Match) -> str:
                return outputs.get(f"step-{m.group(1)}", "")
            rendered[key] = _STEP_REF.sub(repl, value)
        else:
            rendered[key] = value
    return rendered


def run_task(
    raw_input: str,
    router: ModelRouter | None = None,
    db: TaskLogger | None = None,
    events: EventLog | None = None,
    ui: RuntimeUI | None = None,
    budget_config: TokenBudgetConfig | None = None,
    own_db: bool = False,
    dispatcher: Any | None = None,
) -> str:
    """跑一条任务闭环，返回 task_id。

    dispatcher 注入时（BuiltinTools.call）走真执行；否则 W1 stub。
    own_db=True 时由本函数负责 close（CLI 调用用；测试注入 db 时为 False）。
    """
    router = router or build_stub_router()
    ui = ui or RuntimeUI()
    db = db or TaskLogger()
    events = events or EventLog()
    task_id = f"task-{uuid.uuid4().hex[:8]}"
    budget = TokenBudget(config=budget_config, task_id=task_id)
    real_mode = dispatcher is not None

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

        pipeline = build_pipeline(approved=True, dispatcher=dispatcher if real_mode else None)
        outputs: dict[str, str] = {}
        all_ok = True
        for step in plan["steps"]:
            args = _render_args(step.get("args", {}), outputs)
            exec_step = {**step, "args": args}
            ctx = StepContext(task_id=task_id, step=exec_step, goal=plan["goal"])
            results = []
            for stage in pipeline:
                result = stage.run(ctx)
                results.append({"stage": stage.name, "ok": result.ok, "detail": result.detail})
                if not result.ok:
                    break  # 单步失败默认中止（ARCHITECTURE §3）
            step_ok = all(r["ok"] for r in results)
            all_ok = all_ok and step_ok
            ui.show_intent(ctx.execute)
            if real_mode:
                detail = ctx.execute or {}
                stdout = (detail.get("output") or {}).get("stdout", "")
                step_id = step.get("step_id", "")
                if step_id:
                    outputs[step_id] = stdout
                db.log_action(
                    task_id, "execute_real", detail.get("verdict", "UNKNOWN"), now(),
                    step_id=step_id, tool=step.get("tool"),
                    args=args, result={"pipeline": results, "stdout": stdout},
                )
                if not step_ok:
                    db.log_action(
                        task_id, "step_failed", "FAILED", now(),
                        step_id=step.get("step_id"), tool=step.get("tool"),
                        result={"verdict": detail.get("verdict"), "error": detail.get("error")},
                    )
                    break
            else:
                db.log_action(
                    task_id, "execute_stub", "STUB", now(),
                    step_id=step.get("step_id"), tool=step.get("tool"),
                    args=args, result={"pipeline": results},
                )

        final_state = "DONE" if all_ok else "FAILED"
        events.append(task_id, final_state, {
            "backend": route.backend, "usage": usage_snap, "real": real_mode,
        })
        db.update_status(task_id, final_state, now())
        ui.info(f"完成。task_id={task_id} 状态={final_state}")
        return task_id
    finally:
        if own_db:
            db.close()
