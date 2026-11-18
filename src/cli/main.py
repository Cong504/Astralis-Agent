"""CLI 交互层（Interface，ARCHITECTURE.md §2）。

只负责收发与展示：输入任务 → 调 Runtime → 展示计划 → 确认/取消 → 打印执行意图。
状态机、Token 预算、日志由 Runtime 层负责；本层不承载任何编排逻辑。
云端 10–30s 延迟期间显示等待进度反馈（ADR-0003）。
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any, TextIO

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.db import TaskLogger  # noqa: E402
from data.events import EventLog  # noqa: E402
from model.router import ModelRouter  # noqa: E402
from runtime.task_runner import RuntimeUI, run_task as runtime_run_task  # noqa: E402


def render_plan(plan: dict[str, Any]) -> str:
    lines = [f"任务目标: {plan['goal']}", "步骤计划:"]
    for step in plan["steps"]:
        confirm = "  [需二次确认]" if step.get("risk_level") == "high" else ""
        lines.append(
            f"  [{step['step_id']}] {step['action']} "
            f"(tool={step['tool']}, risk={step['risk_level']}){confirm}"
        )
    return "\n".join(lines)


class ProgressTicker:
    """Interface 层等待反馈：后台每 interval 秒打印已耗时，模型调用结束即停。"""

    def __init__(self, stdout: TextIO, interval: float = 5.0):
        self.stdout = stdout
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        started = time.monotonic()

        def tick() -> None:
            while not self._stop.wait(self.interval):
                elapsed = int(time.monotonic() - started)
                print(f"  …正在思考，已耗时 {elapsed} 秒（云端 API 通常需要 10–30 秒）",
                      file=self.stdout, flush=True)

        self._thread = threading.Thread(target=tick, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)


def build_cli_ui(stdin: TextIO, stdout: TextIO, confirm: bool, interval: float) -> RuntimeUI:
    ticker = ProgressTicker(stdout, interval)

    def ask_confirm() -> bool:
        if not confirm:
            return True
        print("确认执行？(y/n): ", end="", file=stdout, flush=True)
        return (stdin.readline() or "").strip().lower() in ("y", "yes")

    return RuntimeUI(
        show_plan=lambda plan: print(render_plan(plan), file=stdout),
        ask_confirm=ask_confirm,
        progress_start=ticker.start,
        progress_stop=ticker.stop,
        show_intent=lambda intent: print(
            f"  [W1 桩] 本应执行: {intent['tool']} {intent['args']} (未真跑)", file=stdout,
        ),
        alert=lambda msg: print(f"[告警] {msg}", file=stdout, flush=True),
        info=lambda msg: print(msg, file=stdout),
    )


def _progress_interval() -> float:
    try:
        with open(Path(__file__).resolve().parents[2] / "config.json", encoding="utf-8") as fh:
            return float(json.load(fh).get("runtime", {}).get("progress_feedback_interval_sec", 5))
    except (OSError, json.JSONDecodeError, ValueError):
        return 5.0


def run_task(
    raw_input: str,
    router: ModelRouter | None = None,
    db_path: Path | None = None,
    events_path: Path | None = None,
    confirm: bool = True,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> str:
    """CLI 入口：构造 Interface 回调，委托 Runtime 执行。返回 task_id。"""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    ui = build_cli_ui(stdin, stdout, confirm, _progress_interval())
    return runtime_run_task(
        raw_input,
        router=router,
        db=TaskLogger(db_path) if db_path else None,
        events=EventLog(events_path) if events_path else None,
        ui=ui,
        own_db=True,
    )


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
