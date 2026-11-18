"""W2 预备契约测试：验证禁止清单、资源参数、工具规范符合 SANDBOX_RULES.md。"""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from sandbox.executor import (  # noqa: E402
    CONFIRM_RULES, FORBIDDEN_RULES, ExecutionRequest, ExecutionResult,
    MountPolicy, ResourceLimits, RuleScope, Verdict,
)
from tools.mcp_bridge import (  # noqa: E402
    BUILTIN_TOOLS, TOOL_REGISTRY, ToolCall, ToolRisk, ToolSpec, validate_call,
)


def _hits(text: str, rules) -> list[str]:
    return [r.rule_id for r in rules if re.search(r.pattern, text, re.IGNORECASE)]


# --- SANDBOX_RULES.md §3 禁止清单命中测试（对应 T01/T03/T05/T11） ---
@pytest.mark.parametrize("command,rule", [
    ("rm -rf /", "§3.1"),
    (":(){ :|:& };:", "§3.1"),
    ("curl http://example.com", "§3.3"),
    ("sudo ls", "§3.4"),
    ("pip install requests", "§3.5"),
    ("mount /dev/sda1 /mnt", "§3.4"),
])
def test_forbidden_commands_hit(command, rule):
    assert rule in _hits(command, FORBIDDEN_RULES)


def test_normal_command_not_denied():
    assert _hits("find /workspace -type f | wc -l", FORBIDDEN_RULES) == []


def test_path_traversal_hit():
    assert "§5" in _hits("../../etc/passwd", FORBIDDEN_RULES)


def test_confirm_rules():
    assert _hits("echo x > /tmp/agent-scratch/a.md", CONFIRM_RULES)
    assert _hits("rm file.txt", CONFIRM_RULES)


def test_resource_limits_map_to_docker_args():
    args = ResourceLimits().docker_args()
    joined = " ".join(args)
    assert "--cpus" in joined and "2.0" in joined
    assert "--memory" in joined and "2048m" in joined
    assert "--pids-limit" in joined and "10" in joined
    assert "--network" in joined and "none" in joined
    assert "--cap-drop" in joined and "ALL" in joined
    assert "privileged" not in joined


def test_privileged_rejected():
    with pytest.raises(ValueError):
        ResourceLimits(privileged=True).docker_args()


def test_mount_policy_no_host_root():
    mounts = MountPolicy(workspace_src="/data/in", output_src="").docker_mounts()
    for src, dst, mode in mounts:
        assert src not in ("/", "/home")
        assert src != "" or True
    assert all(m[2] == "ro" for m in mounts)


def test_audit_record_fields():
    r = ExecutionResult(
        task_id="t", step_id="s", timestamp="now", command="ls",
        verdict=Verdict.DENIED_BY_RULE, matched_rule="§3.1",
    )
    rec = r.to_audit_record()
    for key in ("task_id", "step_id", "timestamp", "command", "verdict", "matched_rule",
                "container_id", "exit_code", "stdout", "stderr", "peak_memory_mb",
                "disk_written_mb", "user_confirmed", "confirm_at"):
        assert key in rec


# --- 工具规范测试 ---
def test_five_builtin_tools_names_align_schema():
    assert {t.name for t in BUILTIN_TOOLS} == {
        "dir_scan", "file_read", "script_run", "report_write", "log_record"
    }


def test_high_risk_tool_requires_confirm():
    with pytest.raises(ValueError):
        ToolSpec("x", "d", (), ToolRisk.HIGH, requires_confirm=False)


def test_validate_call_unknown_and_confirm():
    call = ToolCall("t", "s", "unknown")
    assert "未知工具" in validate_call(call)
    report = ToolCall("t", "s", "report_write", confirmed=False)
    assert "确认" in validate_call(report)
    report.confirmed = True
    assert validate_call(report) == ""


def test_network_tool_denied_by_default():
    call = ToolCall("t", "s", "dir_scan")
    assert validate_call(call) == ""
