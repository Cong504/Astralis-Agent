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
from model.router import ModelRouter, build_cloud_router, build_stub_router  # noqa: E402
from runtime.task_runner import RuntimeUI, run_task as runtime_run_task  # noqa: E402
from sandbox.docker_executor import DockerSandboxExecutor  # noqa: E402
from sandbox.executor import MountPolicy  # noqa: E402
from tools.builtin import BuiltinTools  # noqa: E402


def render_plan(plan: dict[str, Any]) -> str:
    lines = [f"任务目标: {plan['goal']}", "步骤计划:"]
    for step in plan["steps"]:
        confirm = "  [需二次确认]" if step.get("risk_level") == "high" else ""
        lines.append(
            f"  [{step['step_id']}] {step['action']} "
            f"(tool={step['tool']}, risk={step['risk_level']}){confirm}"
        )
    return "\n".join(lines)


def _format_step(intent: dict[str, Any]) -> str:
    """区分 W1 桩与 W2 真执行的步骤展示。"""
    if intent.get("stub", True):
        return f"  [W1 桩] 本应执行: {intent.get('tool')} {intent.get('args')} (未真跑)"
    out = (intent.get("output") or {})
    snippet = str(out.get("stdout", "")).strip().replace("\n", " ")[:80]
    return (f"  [真执行] tool={intent.get('tool')} verdict={intent.get('verdict')} "
            f"exit={out.get('exit_code')} stdout='{snippet}'")


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

    def ask_step_confirm(step, reason) -> bool:
        if not confirm:  # --no-confirm 冒烟模式：任务级与步骤级一并跳过（仅测试用）
            return True
        print(f"  [二次确认] {reason}：{step.get('action')}（{step.get('tool')}）确认？(y/n): ",
              end="", file=stdout, flush=True)
        return (stdin.readline() or "").strip().lower() in ("y", "yes")

    return RuntimeUI(
        show_plan=lambda plan: print(render_plan(plan), file=stdout),
        ask_confirm=ask_confirm,
        progress_start=ticker.start,
        progress_stop=ticker.stop,
        show_intent=lambda intent: print(_format_step(intent), file=stdout),
        alert=lambda msg: print(f"[告警] {msg}", file=stdout, flush=True),
        info=lambda msg: print(msg, file=stdout),
        ask_step_confirm=ask_step_confirm,
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
    real: bool = False,
    workspace: str | None = None,
    output: str | None = None,
) -> str:
    """CLI 入口：构造 Interface 回调，委托 Runtime 执行。返回 task_id。

    real=True 时接真沙箱（五段管道 execute 段换 SandboxExecuteStage），
    模型路由用 build_cloud_router（有 Key 走真云端，无 Key 退 stub 并标注）。
    """
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    if router is None:
        router = build_cloud_router() if real else build_stub_router()
    backend_name = router.primary.name

    dispatcher = None
    if real:
        mounts = MountPolicy(
            workspace_src=str(Path(workspace).resolve()) if workspace else "",
            output_src=str(Path(output).resolve()) if output else "",
        )
        mounts.validate()
        tools = BuiltinTools(DockerSandboxExecutor(), task_id="cli", mounts=mounts)
        dispatcher = tools.call

    print(f"[后端] 模型={backend_name}（{'真实云端' if backend_name != 'stub' else 'stub 桩：无 Key'}）"
          f" | 执行={'真沙箱' if real else '桩（未真跑）'}", file=stdout)

    ui = build_cli_ui(stdin, stdout, confirm, _progress_interval())
    return runtime_run_task(
        raw_input,
        router=router,
        db=TaskLogger(db_path) if db_path else None,
        events=EventLog(events_path) if events_path else None,
        ui=ui,
        own_db=True,
        dispatcher=dispatcher,
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

    run_p = sub.add_parser("run", help="运行一个任务")
    run_p.add_argument("task", help="任务自然语言描述")
    run_p.add_argument("--no-confirm", action="store_true", help="跳过确认（仅冒烟测试用）")
    run_p.add_argument("--real", action="store_true", help="接真沙箱执行（W2）；默认 W1 桩不真跑")
    run_p.add_argument("--workspace", help="宿主输入目录（真执行时只读挂到 /workspace）")
    run_p.add_argument("--output", help="宿主输出目录（真执行时确认后可写挂到 /output）")

    log_p = sub.add_parser("log", help="按 task_id 回放完整轨迹")
    log_p.add_argument("task_id")

    args = parser.parse_args(argv)
    if args.cmd == "run":
        run_task(args.task, confirm=not args.no_confirm, real=args.real,
                 workspace=args.workspace, output=args.output)
    elif args.cmd == "log":
        replay_task(args.task_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
