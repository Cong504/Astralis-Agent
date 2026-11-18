"""W2.7 Runtime 真执行模式测试（mock dispatcher，验证数据流与管道替换）。"""

import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from data.db import TaskLogger  # noqa: E402
from data.events import EventLog  # noqa: E402
from model.router import ModelRouter, TokenUsage  # noqa: E402
from pipeline.contract import SandboxExecuteStage  # noqa: E402
from runtime.task_runner import RuntimeUI, _render_args, build_pipeline, run_task  # noqa: E402
from tools.mcp_bridge import ToolResult  # noqa: E402


class EchoPlan:
    name = "echo"
    last_usage = TokenUsage()

    def generate(self, prompt):
        import json
        return json.dumps({
            "task_id": "x", "goal": "g",
            "steps": [
                {"step_id": "step-1", "action": "scan", "tool": "dir_scan",
                 "args": {"path": "/workspace"}, "risk_level": "low"},
                {"step_id": "step-2", "action": "write", "tool": "report_write",
                 "args": {"target": "/output/r.md", "content": "DATA={{step-1.stdout}}"},
                 "risk_level": "medium"},
            ],
        }, ensure_ascii=False)


def test_render_args_substitutes_prior_stdout():
    rendered = _render_args({"content": "A={{step-1.stdout}}"}, {"step-1": "file1\n"})
    assert rendered["content"] == "A=file1\n"


def test_build_pipeline_uses_sandbox_stage_when_dispatcher():
    pipeline = build_pipeline(True, dispatcher=lambda c: None)
    assert any(isinstance(s, SandboxExecuteStage) for s in pipeline)


def test_real_mode_data_flow_and_logging(tmp_path):
    seen = []

    def dispatcher(call):
        seen.append(dict(call.args))
        if call.tool == "dir_scan":
            return ToolResult(tool=call.tool, ok=True, output={"stdout": "notes.txt\ndata.csv\n"}, verdict="ALLOWED")
        return ToolResult(tool=call.tool, ok=True, output={"stdout": "written"}, verdict="ALLOWED")

    dbp = tmp_path / "a.db"
    ep = tmp_path / "e.jsonl"
    tid = run_task(
        "x", router=ModelRouter(primary=EchoPlan()),
        db=TaskLogger(dbp), events=EventLog(ep),
        ui=RuntimeUI(), dispatcher=dispatcher,
    )
    # step-2 的 content 已替换为 step-1 输出
    assert seen[1]["content"] == "DATA=notes.txt\ndata.csv\n"
    with TaskLogger(dbp) as db:
        r = db.replay(tid)
        assert r["task"]["status"] == "DONE"
        acts = [a for a in r["actions"] if a["action"] == "execute_real"]
        assert len(acts) == 2
    states = [e["state"] for e in EventLog(ep).for_task(tid)]
    assert states[-1] == "DONE"
