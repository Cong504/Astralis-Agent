"""A1' 归一化 + A3 Stdio 握手测试。"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from model.parser import normalize_plan  # noqa: E402
from tools.mcp_bridge import ToolCall  # noqa: E402
from tools.stdio_bridge import StdioMCPBridge  # noqa: E402


def test_normalize_numeric_step_id():
    raw = json.dumps({"task_id": "t", "goal": "g", "steps": [
        {"step_id": 1, "action": "a", "tool": "dir_scan", "args": {}, "risk_level": "low"},
    ]})
    plan, report = normalize_plan(raw)
    assert plan["steps"][0]["step_id"] == "step-1"
    assert any("step_id" in r for r in report["rewrites"])
    assert not report["raw_valid"]


def test_normalize_tool_alias():
    raw = json.dumps({"task_id": "t", "goal": "g", "steps": [
        {"step_id": "step-1", "action": "a", "tool": "filesystem_scan", "args": {}, "risk_level": "low"},
    ]})
    plan, report = normalize_plan(raw)
    assert plan["steps"][0]["tool"] == "dir_scan"
    assert not report["raw_valid"]


def test_normalize_unknown_tool_safe_downgrade():
    raw = json.dumps({"task_id": "t", "goal": "g", "steps": [
        {"step_id": "step-1", "action": "a", "tool": "user_input", "args": {}, "risk_level": "low"},
    ]})
    plan, report = normalize_plan(raw)
    assert plan["steps"][0]["tool"] == "log_record"
    assert any("安全降级" in r for r in report["rewrites"])


def test_normalize_keeps_valid_plan_unchanged():
    raw = json.dumps({"task_id": "t", "goal": "g", "steps": [
        {"step_id": "step-1", "action": "a", "tool": "dir_scan", "args": {"path": "/workspace"}, "risk_level": "low"},
    ]})
    plan, report = normalize_plan(raw)
    assert report["raw_valid"] and report["rewrites"] == []
    assert plan["steps"][0]["args"]["path"] == "/workspace"


def test_stdio_handshake_real_process():
    bridge = StdioMCPBridge()
    try:
        init = bridge.start()
        tools = bridge.list_tools()
        result = bridge.call(ToolCall("a3", "s1", "dir_scan", {"path": "/workspace"}))
        assert init["result"]["protocolVersion"] == "0.1"
        assert len(tools) == 5
        assert result.ok and result.verdict == "ALLOWED"
        assert result.output["echo"]["name"] == "dir_scan"
    finally:
        bridge.shutdown()
