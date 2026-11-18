"""W2.2 执行器骨架测试（mock docker CLI，不依赖真实 daemon）。"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from sandbox.docker_executor import DockerSandboxExecutor  # noqa: E402
from sandbox.executor import ExecutionRequest, Verdict  # noqa: E402


def _proc(code=0, out="", err=""):
    return subprocess.CompletedProcess(args=[], returncode=code, stdout=out, stderr=err)


def _executor_with_calls(calls: list[list[str]], proc_overrides: dict | None = None):
    """记录每次 docker 调用的 argv，返回可复现结果的 runner。"""
    overrides = proc_overrides or {}

    def runner(argv: list[str], timeout):
        calls.append(argv)
        key = argv[1] if len(argv) > 1 else ""
        return overrides.get(key, _proc(out="cid123\n"))

    return DockerSandboxExecutor(runner=runner), calls


REQ = ExecutionRequest(task_id="t-1", step_id="step-1", command="find /workspace -type f | wc -l")


def test_pip_install_denied_by_rule():
    ex, _ = _executor_with_calls([])
    req = ExecutionRequest(task_id="t", step_id="s", command="pip install requests")
    verdict, rule = ex.check_command(req)
    assert verdict is Verdict.DENIED_BY_RULE
    assert rule == "§3.5"


def test_apt_denied_by_rule():
    ex, _ = _executor_with_calls([])
    verdict, rule = ex.check_command(ExecutionRequest("t", "s", "apt-get update"))
    assert verdict is Verdict.DENIED_BY_RULE and rule == "§3.5"


def test_rm_rf_root_denied():
    ex, _ = _executor_with_calls([])
    verdict, rule = ex.check_command(ExecutionRequest("t", "s", "rm -rf /"))
    assert verdict is Verdict.DENIED_BY_RULE and rule == "§3.1"


def test_path_traversal_denied():
    ex, _ = _executor_with_calls([])
    verdict, rule = ex.check_command(ExecutionRequest("t", "s", "cat ../../etc/passwd"))
    assert verdict is Verdict.DENIED_BY_RULE and rule in ("§5", "§3.4")


def test_normal_command_allowed():
    ex, _ = _executor_with_calls([])
    assert ex.check_command(REQ)[0] is Verdict.ALLOWED


def test_confirm_rule_needs_confirm():
    ex, _ = _executor_with_calls([])
    req = ExecutionRequest("t", "s", "echo x > out.txt")
    assert ex.check_command(req)[0] is Verdict.NEED_CONFIRM
    req.user_confirmed = True
    assert ex.check_command(req)[0] is Verdict.ALLOWED


def test_denied_never_creates_container():
    ex, calls = _executor_with_calls([])
    req = ExecutionRequest("t", "s", "pip install requests")
    result = ex.execute(req)
    assert result.verdict is Verdict.DENIED_BY_RULE
    assert calls == []  # 没有任何 docker 调用


def test_create_uses_limits_and_network_none():
    ex, calls = _executor_with_calls([])
    ex.create_container(REQ)
    argv = calls[0]
    joined = " ".join(argv)
    assert "--network" in joined and "none" in joined
    assert "--memory" in joined and "2048m" in joined
    assert "--user" in joined and "10001" in joined
    assert "--cap-drop" in joined and "ALL" in joined
    assert "/bin/bash" in joined


def test_execute_creates_runs_and_destroys():
    calls: list[list[str]] = []
    overrides = {
        "create": _proc(out="cid123\n"),
        "start": _proc(),
        "wait": _proc(out="0\n"),
        "logs": _proc(out="ok\n"),
        "stats": _proc(out="12MiB / 2GiB\t3MB / 0B\n"),
        "rm": _proc(),
    }

    def runner(argv, timeout):
        calls.append(argv)
        return overrides.get(argv[1], _proc())

    ex = DockerSandboxExecutor(runner=runner)
    result = ex.execute(REQ)
    assert result.verdict is Verdict.ALLOWED
    assert result.exit_code == 0
    assert result.peak_memory_mb == 12  # stats 采集并入 run
    cmds = [c[1] for c in calls]
    assert cmds == ["create", "start", "wait", "logs", "stats", "rm"]  # 结束即销毁


def test_timeout_kills_container():
    calls: list[list[str]] = []

    def runner(argv, timeout):
        calls.append(argv)
        if argv[1] == "wait":
            raise subprocess.TimeoutExpired(cmd=argv, timeout=timeout)
        return _proc(out="cid123\n")

    ex = DockerSandboxExecutor(runner=runner)
    result = ex.execute(REQ)
    assert result.verdict is Verdict.TIMEOUT
    assert any(c[1] == "kill" for c in calls)
    assert any(c[1] == "rm" for c in calls)  # 超时也销毁
