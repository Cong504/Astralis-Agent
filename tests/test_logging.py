"""1.7 日志层测试：SQLite 三表回放 + append-only 事件流对齐。"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from data.db import TaskLogger  # noqa: E402
from data.events import EventLog  # noqa: E402


def test_sqlite_replay(tmp_path):
    db = tmp_path / "test.db"
    with TaskLogger(db) as log:
        log.create_task("t-1", "统计目录", "RECEIVED", "2026-11-17T10:00:00+08:00")
        log.save_plan("t-1", "统计目录", {"goal": "x", "steps": []}, "2026-11-17T10:00:01+08:00")
        log.log_action("t-1", "print_intent", "STUB", "2026-11-17T10:00:02+08:00", step_id="step-1", tool="dir_scan")
        log.update_status("t-1", "DONE", "2026-11-17T10:00:03+08:00")
        r = log.replay("t-1")
        assert r["task"]["status"] == "DONE"
        assert len(r["plans"]) == 1
        assert len(r["actions"]) == 1


def test_events_append_only(tmp_path):
    path = tmp_path / "events.jsonl"
    el = EventLog(path)
    e1 = el.append("t-1", "RECEIVED", {"raw": "x"})
    e2 = el.append("t-1", "AWAIT_CONFIRM", {})
    assert e1["seq"] == 1 and e2["seq"] == 2
    assert len(el.for_task("t-1")) == 2
    # 追加不覆盖历史
    assert el.for_task("t-1")[0]["state"] == "RECEIVED"


def test_events_reject_invalid_state(tmp_path):
    el = EventLog(tmp_path / "events.jsonl")
    try:
        el.append("t-1", "NOT_A_STATE")
        assert False, "应拒绝非法状态"
    except ValueError:
        pass


def test_alignment(tmp_path):
    """同一 task_id 能同时查到 SQLite 链路与事件流。"""
    db = tmp_path / "test.db"
    ep = tmp_path / "events.jsonl"
    with TaskLogger(db) as log:
        log.create_task("align-1", "x", "RECEIVED", "2026-11-17T10:00:00+08:00")
        assert log.replay("align-1")["task"]["task_id"] == "align-1"
    el = EventLog(ep)
    el.append("align-1", "RECEIVED")
    assert el.for_task("align-1")[0]["task_id"] == "align-1"
