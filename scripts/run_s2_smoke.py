"""S2 端到端冒烟（W2.7）：扫描目录 → 统计 → 生成 Markdown 报告 → 落库。

真实链路：Runtime 五段管道 → SandboxExecuteStage → BuiltinTools →
DockerSandboxExecutor（真 Docker）→ 报告写入宿主输出目录 → SQLite/事件流落库。

模型用 stub 计划（无云端 Key，日志标注 backend=stub），但沙箱执行是真容器——
这是 W2 首次纵向点亮「计划→确认→真执行→日志」整机。

用法：
  python scripts/run_s2_smoke.py
输出：task_id、报告路径、每步耗时、create→首条命令返回的冷启动耗时。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from compat.encoding import force_utf8_stdio  # noqa: E402

force_utf8_stdio()

from data.db import TaskLogger  # noqa: E402
from data.events import EventLog  # noqa: E402
from model.router import StubBackend, TokenUsage  # noqa: E402
from runtime.task_runner import RuntimeUI, run_task  # noqa: E402
from sandbox.executor import MountPolicy  # noqa: E402
from sandbox.docker_executor import DockerSandboxExecutor  # noqa: E402
from tools.builtin import BuiltinTools  # noqa: E402


class TimedExecutor(DockerSandboxExecutor):
    """记录 create 与首条命令返回的耗时（冷启动基线）。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.create_seconds: float | None = None
        self.first_cmd_seconds: float | None = None
        self._t0 = 0.0
        self._t_create = 0.0
        self._calls = 0

    def create_container(self, request):
        self._t0 = time.perf_counter()
        cid = super().create_container(request)
        self._t_create = time.perf_counter()
        if self.create_seconds is None:
            self.create_seconds = self._t_create - self._t0
        return cid

    def run(self, container_id, request):
        result = super().run(container_id, request)
        self._calls += 1
        if self.first_cmd_seconds is None:
            self.first_cmd_seconds = time.perf_counter() - self._t0  # create→命令返回
        return result

    def run_capture(self, command: str, mounts: "MountPolicy") -> str:
        """用真容器执行一条只读命令并返回 stdout（用于报告内容自证）。"""
        from sandbox.executor import ExecutionRequest
        req = ExecutionRequest(
            task_id="s2-verify", step_id="verify", command=command,
            mounts=mounts, user_confirmed=True,
        )
        cid = self.create_container(req)
        try:
            return self.run(cid, req).stdout
        finally:
            self.destroy(cid)


class S2PlanBackend(StubBackend):
    """S2 真实计划：dir_scan → 生成报告内容 → report_write 到 /output。"""

    def __init__(self):
        super().__init__()
        self._plan = {
            "task_id": "s2-smoke",
            "goal": "扫描 /workspace 并生成 Markdown 报告落库",
            "steps": [
                {
                    "step_id": "step-1",
                    "action": "扫描工作目录文件列表",
                    "tool": "dir_scan",
                    "args": {"path": "/workspace"},
                    "risk_level": "low",
                },
                {
                    "step_id": "step-2",
                    "action": "把扫描结果写成 Markdown 报告到 /output/report.md",
                    "tool": "report_write",
                    "args": {
                        "target": "/output/report.md",
                        "content": "# 目录扫描报告\n\n结果：\n{{step-1.stdout}}\n",
                    },
                    "risk_level": "medium",
                },
            ],
        }

    def generate(self, prompt: str) -> str:
        self.last_usage = TokenUsage()
        return json.dumps(self._plan, ensure_ascii=False)


def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="astralis-s2-work-")).resolve()
    outdir = Path(tempfile.mkdtemp(prefix="astralis-s2-out-")).resolve()
    (workdir / "notes.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    (workdir / "data.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (workdir / "sub").mkdir()
    (workdir / "sub" / "deep.md").write_text("# deep\n", encoding="utf-8")

    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    db = TaskLogger(logs / "agent.db")
    events = EventLog(logs / "events.jsonl")

    mounts = MountPolicy(
        workspace_src=str(workdir),
        workspace_dst="/workspace",
        output_src=str(outdir),
        output_dst="/output",
    )
    executor = TimedExecutor()
    tools = BuiltinTools(executor, task_id="s2", mounts=mounts)

    ui = RuntimeUI(
        show_plan=lambda p: print("计划:", p["goal"]),
        info=lambda m: print(m),
        alert=lambda m: print("[告警]", m),
        ask_step_confirm=lambda step, reason: True,  # 冒烟：§4 步骤级确认自动放行
    )

    from model.router import ModelRouter
    router = ModelRouter(primary=S2PlanBackend())

    t_start = time.perf_counter()
    task_id = run_task(
        "扫描目录并生成 Markdown 报告",
        router=router, db=db, events=events, ui=ui,
        own_db=False, dispatcher=tools.call,
    )
    wall = time.perf_counter() - t_start

    replay = db.replay(task_id)
    status = replay["task"]["status"]
    print(f"\n[S2] task_id={task_id} status={status}")
    print(f"[S2] 总耗时(含计划/确认): {wall:.2f}s")
    print(f"[S2] 冷启动 create: {executor.create_seconds:.3f}s" if executor.create_seconds else "[S2] 冷启动未记录")
    print(f"[S2] create→首条命令返回: {executor.first_cmd_seconds:.3f}s" if executor.first_cmd_seconds else "[S2] 首命令耗时未记录")

    report = outdir / "report.md"
    print(f"[S2] 报告路径: {report}（宿主存在={report.exists()}）")

    # 容器内读回报告验证内容（跨平台自证，避免 Windows 挂载卷宿主读取权限问题）
    verify = executor.run_capture((
        f"cat /output/report.md 2>/dev/null || echo __NO_REPORT__"
    ), mounts)
    host_readable = False
    if report.exists():
        try:
            host_readable = "notes.txt" in report.read_text(encoding="utf-8")
        except PermissionError:
            host_readable = False  # Windows Docker Desktop 挂载卷 uid 映射限制
    container_ok = "notes.txt" in verify and "__NO_REPORT__" not in verify
    print(f"[S2] 容器内读回报告: {'OK' if container_ok else 'FAIL'}")
    print(f"[S2] 宿主直接读取: {'OK' if host_readable else 'N/A（Windows 挂载 uid 限制，不作判据）'}")
    if container_ok:
        print("[S2] 报告内容（容器内读回，前 200 字）:")
        print(verify[:200])

    states = [e["state"] for e in events.for_task(task_id)]
    print(f"[S2] 状态跃迁: {' -> '.join(states)}")

    ok = status == "DONE" and container_ok
    db.close()
    print(f"[S2] {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
