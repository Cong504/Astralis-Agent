"""W1 集成冒烟（1.9）：输入→计划→确认/取消→桩执行→日志回放。"""

import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from cli.main import run_task  # noqa: E402
from data.db import TaskLogger  # noqa: E402
from data.events import EventLog  # noqa: E402
from model.router import build_stub_router  # noqa: E402


def test_confirm_path_full_chain(tmp_path):
    dbp = tmp_path / "agent.db"
    ep = tmp_path / "events.jsonl"
    out = io.StringIO()
    tid = run_task(
        "统计指定目录下的文件数量与类型分布",
        router=build_stub_router(),
        db_path=dbp, events_path=ep,
        confirm=True, stdin=io.StringIO("y\n"), stdout=out,
    )
    s = out.getvalue()
    assert "步骤计划" in s and "本应执行" in s and "未真跑" in s
    with TaskLogger(dbp) as log:
        r = log.replay(tid)
        assert r["task"]["status"] == "DONE"
        assert len(r["plans"]) == 1
        assert len(r["actions"]) == 2
    states = [e["state"] for e in EventLog(ep).for_task(tid)]
    assert states == ["RECEIVED", "PLANNING", "AWAIT_CONFIRM", "EXECUTING", "DONE"]


def test_cancel_path_logs_cancelled(tmp_path):
    dbp = tmp_path / "agent.db"
    ep = tmp_path / "events.jsonl"
    out = io.StringIO()
    tid = run_task(
        "统计目录",
        router=build_stub_router(),
        db_path=dbp, events_path=ep,
        confirm=True, stdin=io.StringIO("n\n"), stdout=out,
    )
    assert "已取消" in out.getvalue()
    with TaskLogger(dbp) as log:
        assert log.replay(tid)["task"]["status"] == "CANCELLED"
    states = [e["state"] for e in EventLog(ep).for_task(tid)]
    assert states[-1] == "CANCELLED"
    assert "EXECUTING" not in states
