"""W3 3.7 回放补强测试：denials 段 + 复盘段端到端。"""

import io
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from cli.main import replay_task  # noqa: E402
from data.db import TaskLogger  # noqa: E402


def test_replay_includes_denials_and_reflection(tmp_path):
    dbp = tmp_path / "a.db"
    db = TaskLogger(dbp)
    db.create_task("t-replay", "scan", "DONE", "now")
    db.log_denial("t-replay", "pip install requests", "DENIED_BY_RULE", "now",
                  step_id="step-1", matched_rule="§3.5")
    db.log_action("t-replay", "reflection", "OK", "now", result={
        "success_points": "成功拦截",
        "failure_points": "无",
        "improvement": "补白名单",
    })
    db.close()

    out = io.StringIO()
    replay_task("t-replay", db_path=dbp, events_path=tmp_path / "e.jsonl", stdout=out)
    text = out.getvalue()
    assert "拦截事件" in text
    assert "DENIED_BY_RULE" in text and "§3.5" in text
    assert "复盘:" in text
    assert "成功拦截" in text and "补白名单" in text


def test_replay_without_denials_still_works(tmp_path):
    dbp = tmp_path / "a.db"
    db = TaskLogger(dbp)
    db.create_task("t-plain", "scan", "DONE", "now")
    db.close()
    out = io.StringIO()
    replay_task("t-plain", db_path=dbp, events_path=tmp_path / "e.jsonl", stdout=out)
    assert "状态: DONE" in out.getvalue()
