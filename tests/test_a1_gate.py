"""W3 3.8 G1 门禁测试：触发路径判定 + 无 Key 显式 SKIP 留痕。"""

import importlib.util
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

spec = importlib.util.spec_from_file_location("gate", ROOT / "scripts" / "gate_a1_regression.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def test_trigger_paths_defined():
    assert "src/runtime/task_runner.py" in gate.TRIGGER_PATHS
    assert "src/schema/plan.schema.json" in gate.TRIGGER_PATHS
    assert "src/model/parser.py" in gate.TRIGGER_PATHS
    assert "src/tools/builtin.py" in gate.TRIGGER_PATHS


def test_trigger_match(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    (tmp_path / "logs").mkdir()
    monkeypatch.setattr(gate, "has_key", lambda: False)  # 屏蔽真 API，单测不烧配额
    result = {}
    monkeypatch.setattr(gate, "record", lambda r: result.update(r))
    exit_code = gate.main(["src/runtime/task_runner.py"])
    assert exit_code == 0
    # 命中触发路径 → 进入门禁分支（SKIP 因无 Key），而非 no-relevant-change
    assert result.get("triggered") == ["src/runtime/task_runner.py"]
    assert result["decision"] == "SKIP" and "no cloud key" in result["reason"]


def test_no_relevant_change_skips(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    (tmp_path / "logs").mkdir()
    result = {}
    monkeypatch.setattr(gate, "record", lambda r: result.update(r))
    exit_code = gate.main(["README.md", "docs/foo.md"])
    assert exit_code == 0
    assert result["decision"] == "SKIP" and result["reason"] == "no relevant change"


def test_no_key_skips_explicitly(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    (tmp_path / "logs").mkdir()
    for name in gate.KEY_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(gate, "has_key", lambda: False)
    result = {}
    monkeypatch.setattr(gate, "record", lambda r: result.update(r))
    exit_code = gate.main(["src/tools/builtin.py"])
    assert exit_code == 0  # SKIP 不阻塞，但不静默
    assert result["decision"] == "SKIP"
    assert "not silent" in result["reason"]
    assert result.get("todo")  # 有明确待办
