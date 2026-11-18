"""W3 复盘系统级方案回归测试：全局 UTF-8 防御（防 GBK 重演）。"""

import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from compat.encoding import force_utf8_stdio  # noqa: E402


def test_force_utf8_sets_stdout_encoding():
    force_utf8_stdio()
    enc = getattr(sys.stdout, "encoding", "") or ""
    assert enc.lower().replace("-", "") == "utf8", f"stdout encoding={enc}"


def test_force_utf8_sets_pythonioencoding_env():
    force_utf8_stdio()
    assert os.getenv("PYTHONIOENCODING", "").startswith("utf-8")


def test_force_utf8_is_idempotent():
    force_utf8_stdio()
    first = sys.stdout.encoding
    force_utf8_stdio()
    assert sys.stdout.encoding == first


def test_non_ascii_output_does_not_crash(capsys):
    """核心回归：W2 Docker stdout 与 W3 控制台 print 两次 GBK 崩溃的字符都能输出。"""
    force_utf8_stdio()
    print("成本 ¥0.5 / 中文 / 目录扫描报告")
    captured = capsys.readouterr()
    assert "¥0.5" in captured.out and "目录扫描报告" in captured.out


def test_all_entrypoints_call_global_defense():
    """防止新入口忘记接全局防御：检查每个可执行 script/CLI 都调用 force_utf8_stdio。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    entries = [
        "src/cli/main.py",
        "scripts/run_s2_smoke.py",
        "scripts/bench_cold_start.py",
        "scripts/bench_a1_legality.py",
        "scripts/verify_escape_suite.py",
        "scripts/verify_limits.py",
        "scripts/verify_a3_stdio.py",
        "scripts/gate_a1_regression.py",
        "scripts/verify_cost_guard.py",
    ]
    for rel in entries:
        src = open(os.path.join(root, rel), encoding="utf-8").read()
        assert "force_utf8_stdio" in src, f"入口未接全局 UTF-8 防御: {rel}"
