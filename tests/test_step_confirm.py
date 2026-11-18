"""W3 3.3 §4 步骤级二次确认测试。"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from pipeline.contract import (  # noqa: E402
    ApproveStage, GuardStage, PreExecuteStage, SandboxExecuteStage, StepContext,
    needs_step_confirm,
)
from tools.mcp_bridge import ToolResult  # noqa: E402


def test_needs_confirm_report_write():
    need, reason = needs_step_confirm({"tool": "report_write", "args": {"target": "/output/r.md"}})
    assert need and "§4" in reason


def test_needs_confirm_high_risk():
    need, _ = needs_step_confirm({"tool": "file_read", "risk_level": "high", "args": {}})
    assert need


def test_needs_confirm_script_with_redirect():
    need, _ = needs_step_confirm({"tool": "script_run", "args": {"script": "echo x > out.txt"}})
    assert need


def test_no_confirm_for_read_tools():
    assert not needs_step_confirm({"tool": "dir_scan", "args": {"path": "/workspace"}})[0]
    assert not needs_step_confirm({"tool": "file_read", "args": {"path": "/workspace/a"}})[0]
    assert not needs_step_confirm({"tool": "log_record", "args": {}})[0]


def test_approve_denies_step4_without_confirmer():
    ctx = StepContext("t", {"tool": "report_write", "args": {}})
    stage = ApproveStage(approved=True)  # 无 step_confirmer
    result = stage.run(ctx)
    assert not result.ok
    assert ctx.approve["verdict"] == "NEED_CONFIRM"


def test_approve_allows_step4_when_confirmed():
    ctx = StepContext("t", {"tool": "report_write", "args": {}})
    stage = ApproveStage(approved=True, step_confirmer=lambda step, reason: True)
    assert stage.run(ctx).ok
    assert ctx.approve["verdict"] == "CONFIRMED"


def test_pipeline_stops_before_execute_when_not_confirmed():
    """§4 未确认时，管道在 approve 段中止，execute 段（含建容器）不得执行。"""
    executed = []

    def dispatcher(call):
        executed.append(call.tool)
        return ToolResult(tool=call.tool, ok=True, output={}, verdict="ALLOWED")

    ctx = StepContext("t", {"step_id": "step-1", "tool": "report_write", "args": {}})
    pipeline = [
        PreExecuteStage(), GuardStage(),
        ApproveStage(approved=True, step_confirmer=lambda s, r: False),
        SandboxExecuteStage(dispatcher, confirmed=True),
    ]
    for stage in pipeline:
        result = stage.run(ctx)
        if not result.ok:
            break
    assert executed == []  # execute 段未运行 → 未建容器


def test_task_not_approved_stops_all():
    ctx = StepContext("t", {"tool": "dir_scan", "args": {}})
    result = ApproveStage(approved=False, step_confirmer=lambda s, r: True).run(ctx)
    assert not result.ok and ctx.approve["reason"] == "task_not_approved"
