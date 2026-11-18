"""W3 3.4 复盘生成测试：固定三字段 + 真实可优化点 + rm -fr 案例 + 失败降级。"""

import io
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from data.db import TaskLogger  # noqa: E402
from data.events import EventLog  # noqa: E402
from model.router import ModelRouter  # noqa: E402
from reflection.generator import (  # noqa: E402
    REFLECTION_FIELDS, ReflectionError, generate_reflection,
)
from runtime.task_runner import RuntimeUI, run_task  # noqa: E402
from tools.mcp_bridge import ToolResult  # noqa: E402


def test_reflection_has_fixed_fields():
    r = generate_reflection("t", "DONE", [{"tool": "dir_scan", "verdict": "ALLOWED"}], [])
    assert all(f in r for f in REFLECTION_FIELDS)
    assert r["success_points"] and r["improvement"]


def test_reflection_at_least_one_real_improvement():
    r = generate_reflection("t", "DONE", [{"tool": "x", "verdict": "ALLOWED"}], [],
                            {"total_tokens": 500})
    assert "500 tokens" in r["improvement"]


def test_reflection_includes_rm_fr_chain_gap_case():
    r = generate_reflection("t", "DONE", [], [
        {"verdict": "DENIED_BY_RULE", "matched_rule": "§3.1"}
    ])
    assert "rm -fr" in r["improvement"] and "链式" in r["improvement"]


def test_runtime_writes_reflection_and_reflecting_event(tmp_path):
    class Plan:
        name = "echo"
        last_usage = None

        def generate(self, prompt):
            from model.router import TokenUsage
            self.last_usage = TokenUsage()
            return json.dumps({"task_id": "x", "goal": "g", "steps": [
                {"step_id": "step-1", "action": "a", "tool": "dir_scan",
                 "args": {"path": "/workspace"}, "risk_level": "low"},
            ]}, ensure_ascii=False)

    dbp, ep = tmp_path / "a.db", tmp_path / "e.jsonl"
    tid = run_task("x", router=ModelRouter(primary=Plan()),
                   db=TaskLogger(dbp), events=EventLog(ep), ui=RuntimeUI())
    with TaskLogger(dbp) as db:
        r = db.replay(tid)
        refl = [a for a in r["actions"] if a["action"] == "reflection"]
        assert len(refl) == 1
        data = json.loads(refl[0]["result_json"])
        assert all(f in data for f in REFLECTION_FIELDS)
    states = [e["state"] for e in EventLog(ep).for_task(tid)]
    assert "REFLECTING" in states and states[-1] == "DONE"


def test_reflect_failed_does_not_break_main_flow(tmp_path, monkeypatch):
    """复盘抛异常时标 reflect_failed，任务仍 DONE。"""
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "reflection.generator":
            raise RuntimeError("复盘模块故障")
        return real_import(name, *a, **k)

    class Plan:
        name = "echo"
        last_usage = None

        def generate(self, prompt):
            from model.router import TokenUsage
            self.last_usage = TokenUsage()
            return json.dumps({"task_id": "x", "goal": "g", "steps": [
                {"step_id": "step-1", "action": "a", "tool": "dir_scan",
                 "args": {"path": "/workspace"}, "risk_level": "low"},
            ]}, ensure_ascii=False)

    dbp, ep = tmp_path / "a.db", tmp_path / "e.jsonl"
    monkeypatch.setattr(builtins, "__import__", fake_import)
    tid = run_task("x", router=ModelRouter(primary=Plan()),
                   db=TaskLogger(dbp), events=EventLog(ep), ui=RuntimeUI())
    with TaskLogger(dbp) as db:
        assert db.replay(tid)["task"]["status"] == "DONE"
    events = EventLog(ep).for_task(tid)
    refl = [e for e in events if e["state"] == "REFLECTING"]
    assert refl and refl[-1]["detail"].get("reflect_failed") is True
