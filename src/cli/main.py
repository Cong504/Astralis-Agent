"""CLI 交互层（1.6）。

输入任务 → 生成结构化计划 → 展示 → 确认/取消 → W1 打印本应执行的命令（不真跑）→ 落日志。
确认前不触发任何副作用；取消写 CANCELLED。全部状态跃迁写 append-only 事件流。
"""

from __future__ import annotations

import argparse
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.db import TaskLogger  # noqa: E402
from data.events import EventLog  # noqa: E402
from model.client import generate_plan_with_retry  # noqa: E402
from model.router import ModelRouter, build_stub_router  # noqa: E402
from pipeline.contract import (  # noqa: E402
    ApproveStage, ExecuteStage, GuardStage, PostExecuteStage, PreExecuteStage, StepContext,
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def build_pipeline(approved: bool):
    return [
        PreExecuteStage(),
        GuardStage(),
        ApproveStage(approved=approved),
        ExecuteStage(),
        PostExecuteStage(),
    ]


def render_plan(plan: dict[str, Any]) -> str:
    lines = [f"任务目标: {plan['goal']}", "步骤计划:"]
    for step in plan["steps"]:
        confirm = "  [需二次确认]" if step.get("risk_level") == "high" else ""
        lines.append(
            f"  [{step['step_id']}] {step['action']} "
            f"(tool={step['tool']}, risk={step['risk_level']}){confirm}"
        )
    return "\n".join(lines)


def run_task(
    raw_input: str,
    router: ModelRouter | None = None,
    db_path: Path | None = None,
    events_path: Path | None = None,
    confirm: bool = True,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> str:
    """跑一条任务的 W1 闭环，返回 task_id。"""
    router = router or build_stub_router()
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    task_id = f"task-{uuid.uuid4().hex[:8]}"
    db = TaskLogger(db_path) if db_path else TaskLogger()
    events = EventLog(events_path) if events_path else EventLog()

    try:
        events.append(task_id, "RECEIVED", {"raw_input": raw_input})
        db.create_task(task_id, raw_input, "RECEIVED", now())

        events.append(task_id, "PLANNING", {})
        db.update_status(task_id, "PLANNING", now())

        prompt = (
            "你是任务规划器。只输出 JSON，字段为 task_id/goal/steps，"
            "steps 每项含 step_id/action/tool/args/risk_level，"
            "risk_level 取 low|medium|high，high 时必须有 requires_confirm=true。\n"
            f"task_id: {task_id}\n任务: {raw_input}"
        )
        plan, route = generate_plan_with_retry(router, prompt)
        plan["task_id"] = task_id
        db.save_plan(task_id, plan["goal"], plan, now())

        print(render_plan(plan), file=stdout)
        events.append(task_id, "AWAIT_CONFIRM", {"backend": route.backend, "fallback": route.fallback})
        db.update_status(task_id, "AWAIT_CONFIRM", now())

        if confirm:
            print("确认执行？(y/n): ", end="", file=stdout, flush=True)
            answer = (stdin.readline() or "").strip().lower()
            approved = answer in ("y", "yes")
        else:
            approved = True

        if not approved:
            events.append(task_id, "CANCELLED", {"reason": "user"})
            db.update_status(task_id, "CANCELLED", now())
            print("已取消，日志已记录 CANCELLED。", file=stdout)
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
            intent = ctx.execute
            print(
                f"  [W1 桩] 本应执行: {intent['tool']} {intent['args']} (未真跑)",
                file=stdout,
            )
            db.log_action(
                task_id, "execute_stub", "STUB", now(),
                step_id=step.get("step_id"), tool=step.get("tool"),
                args=step.get("args"), result={"pipeline": results},
            )

        events.append(task_id, "DONE", {"backend": route.backend})
        db.update_status(task_id, "DONE", now())
        print(f"完成。task_id={task_id}", file=stdout)
        return task_id
    finally:
        db.close()


def replay_task(task_id: str, db_path: Path | None = None, events_path: Path | None = None,
                stdout: TextIO | None = None) -> None:
    stdout = stdout or sys.stdout
    db = TaskLogger(db_path) if db_path else TaskLogger()
    try:
        data = db.replay(task_id)
        print(f"=== 任务 {task_id} ===", file=stdout)
        print(f"状态: {data['task']['status']} | 输入: {data['task']['raw_input']}", file=stdout)
        for p in data["plans"]:
            print(f"计划: {p['goal']} ({len(p['plan_json'])} bytes)", file=stdout)
        for a in data["actions"]:
            print(f"动作: {a['action']} step={a['step_id']} tool={a['tool']} status={a['status']}", file=stdout)
    finally:
        db.close()
    el = EventLog(events_path) if events_path else EventLog()
    print("事件流:", file=stdout)
    for e in el.for_task(task_id):
        print(f"  #{e['seq']} {e['state']} {e['timestamp']} {e['detail']}", file=stdout)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent", description="本地 Agent 底盘 v0.1 CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="运行一个任务（W1 不真跑）")
    run_p.add_argument("task", help="任务自然语言描述")
    run_p.add_argument("--no-confirm", action="store_true", help="跳过确认（仅冒烟测试用）")

    log_p = sub.add_parser("log", help="按 task_id 回放完整轨迹")
    log_p.add_argument("task_id")

    args = parser.parse_args(argv)
    if args.cmd == "run":
        run_task(args.task, confirm=not args.no_confirm)
    elif args.cmd == "log":
        replay_task(args.task_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
