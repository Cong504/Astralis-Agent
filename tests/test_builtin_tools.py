"""W2.6 工具 mock 测试：验证派发/路径校验/禁止命令传递，不验证容器内真执行。"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from sandbox.executor import ExecutionRequest, ExecutionResult, Verdict  # noqa: E402
from tools.builtin import BuiltinTools  # noqa: E402
from tools.mcp_bridge import ToolCall  # noqa: E402


class RecordingExecutor:
    """记录收到的 ExecutionRequest，返回 ALLOWED，不碰真实 Docker。"""

    def __init__(self):
        self.requests: list[ExecutionRequest] = []

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        self.requests.append(request)
        return ExecutionResult(
            task_id=request.task_id, step_id=request.step_id, timestamp="now",
            command=request.command, verdict=Verdict.ALLOWED, exit_code=0, stdout="ok",
        )


def _tools():
    ex = RecordingExecutor()
    return BuiltinTools(ex, task_id="t-1"), ex


def test_dir_scan_dispatches_find():
    tools, ex = _tools()
    r = tools.call(ToolCall("t-1", "s1", "dir_scan", {"path": "/workspace/data"}))
    assert r.ok
    assert "find /workspace/data" in ex.requests[0].command


def test_dir_scan_rejects_path_outside_workspace():
    tools, ex = _tools()
    r = tools.call(ToolCall("t-1", "s1", "dir_scan", {"path": "/etc"}))
    assert not r.ok and "非法路径" in r.error
    assert ex.requests == []  # 未派发到沙箱


def test_dir_scan_rejects_traversal():
    tools, ex = _tools()
    r = tools.call(ToolCall("t-1", "s1", "dir_scan", {"path": "/workspace/../etc"}))
    assert not r.ok
    assert ex.requests == []


def test_file_read_dispatches_cat():
    tools, ex = _tools()
    r = tools.call(ToolCall("t-1", "s1", "file_read", {"path": "/workspace/a.txt"}))
    assert r.ok and ex.requests[0].command == "cat /workspace/a.txt"


def test_script_run_passes_script_to_executor():
    tools, ex = _tools()
    r = tools.call(ToolCall("t-1", "s1", "script_run", {"script": "echo hello"}))
    assert r.ok and ex.requests[0].command == "echo hello"


def test_script_run_pip_install_reaches_executor_for_blocking():
    """工具层不自行放行；pip install 作为命令交给 exec，由执行层 DENIED。"""
    tools, ex = _tools()
    tools.call(ToolCall("t-1", "s1", "script_run", {"script": "pip install requests"}))
    assert ex.requests[0].command == "pip install requests"  # 派发，执行层拦截


def test_report_write_requires_confirm_and_scratch_path():
    tools, ex = _tools()
    call = ToolCall("t-1", "s1", "report_write",
                    {"target": "/tmp/agent-scratch/r.md", "content": "hi"}, confirmed=False)
    r = tools.call(call)
    assert not r.ok and "确认" in r.error  # report_write 需确认
    call.confirmed = True
    r = tools.call(call)
    assert r.ok and "base64 -d" in ex.requests[-1].command


def test_report_write_rejects_outside_scratch():
    tools, ex = _tools()
    r = tools.call(ToolCall("t-1", "s1", "report_write",
                            {"target": "/etc/evil", "content": "x"}, confirmed=True))
    assert not r.ok and "/tmp/agent-scratch" in r.error
    assert ex.requests == []


def test_log_record_no_sandbox():
    tools, ex = _tools()
    r = tools.call(ToolCall("t-1", "s1", "log_record", {"event": "x"}))
    assert r.ok and not ex.requests  # 不进沙箱
