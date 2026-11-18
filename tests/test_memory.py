"""W3 3.5 记忆库测试：结构化标签检索，无向量库。"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from data.db import TaskLogger  # noqa: E402
from data.events import EventLog  # noqa: E402
from data.memory import extract_tags, recall, remember  # noqa: E402
from model.router import ModelRouter, TokenUsage  # noqa: E402
from runtime.task_runner import RuntimeUI, run_task  # noqa: E402


def test_extract_tags_from_steps_and_denials():
    tags = extract_tags(
        [{"tool": "dir_scan", "verdict": "ALLOWED"},
         {"tool": "script_run", "verdict": "DENIED_BY_RULE"}],
        [{"matched_rule": "§3.5"}],
        {"improvement": "省 tokens", "failure_points": "无"},
    )
    assert "tool:dir_scan" in tags
    assert "security:denied" in tags
    assert "rule:§3.5" in tags
    assert "cost" in tags


def test_memory_recall_by_tag(tmp_path):
    db = TaskLogger(tmp_path / "m.db")
    remember(db.conn, "t1", ("tool:dir_scan", "cost"), "扫描报告经验", "now")
    remember(db.conn, "t2", ("tool:file_read",), "读取经验", "now")
    found = recall(db.conn, "cost")
    assert len(found) == 1 and found[0]["task_id"] == "t1"
    assert len(recall(db.conn, "tool:dir_scan")) == 1
    db.close()


def test_no_vector_dependency():
    """memory 模块只依赖 sqlite3/json，不 import 任何向量库。"""
    import inspect
    import data.memory as m
    src = inspect.getsource(m)
    assert "chroma" not in src.lower() and "faiss" not in src.lower()


def test_runtime_persists_memory(tmp_path):
    class Plan:
        name = "echo"

        def generate(self, prompt):
            self.last_usage = TokenUsage()
            return json.dumps({"task_id": "x", "goal": "g", "steps": [
                {"step_id": "step-1", "action": "a", "tool": "dir_scan",
                 "args": {"path": "/workspace"}, "risk_level": "low"},
            ]}, ensure_ascii=False)

    dbp, ep = tmp_path / "a.db", tmp_path / "e.jsonl"
    tid = run_task("x", router=ModelRouter(primary=Plan()),
                   db=TaskLogger(dbp), events=EventLog(ep), ui=RuntimeUI())
    db = TaskLogger(dbp)
    rows = recall(db.conn, "tool:dir_scan")
    assert any(r["task_id"] == tid for r in rows)
    db.close()
