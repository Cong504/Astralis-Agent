"""W4 4.1 包导出测试（PACKAGE_SPEC.md §2/§3/§9/§10）。"""

import hashlib
import json
import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from package.exporter import (  # noqa: E402
    AgentSpec, PackageExportError, compute_aggregate_hash,
    compute_permissions_digest, export_package,
)

TOOLS = {
    "protocol": "mcp",
    "tools": [
        {"name": "dir_scan", "source": "builtin"},
        {"name": "report_write", "source": "builtin"},
    ],
    "permissions": {
        "filesystem": {"read": ["/workspace"], "write": ["/tmp/agent-scratch"]},
        "network": {"enabled": False, "allowlist": []},
        "resources": {"cpu": 2, "memory_mb": 2048, "disk_mb": 500, "step_timeout_sec": 60},
        "forbidden_commands_inherited": True,
    },
}


def _spec(**kwargs):
    base = dict(name="file-triage-agent", prompt="# 系统提示词\n扫描目录生成报告。",
                tools=TOOLS, config={"model": {"primary": "deepseek"}},
                skills={"scan_directory": "name: scan_directory\nrisk_level: low\nrequires_confirm: false\n"})
    base.update(kwargs)
    return AgentSpec(**base)


def test_export_has_seven_parts(tmp_path):
    pkg = export_package(_spec(), tmp_path)
    assert pkg.name == "file-triage-agent-v1.0.0.agentpkg"
    with zipfile.ZipFile(pkg) as zf:
        names = set(zf.namelist())
        for required in ("manifest.json", "prompt.md", "tools.json", "config.json",
                         "signature.json", "memory/experiences.json",
                         "skills/scan_directory.yaml"):
            assert required in names, f"缺部分: {required}"


def test_manifest_hash_and_signature(tmp_path):
    pkg = export_package(_spec(), tmp_path)
    with zipfile.ZipFile(pkg) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        sig = json.loads(zf.read("signature.json"))
    assert manifest["hash"]["algorithm"] == "sha256"
    assert len(manifest["hash"]["value"]) == 64
    assert sig["algorithm"] is None and sig["signer"] is None and sig["trust"] == "local-unverified"
    assert sig["permissions_digest"].startswith("sha256:")
    # 与独立重算的 tools.json.permissions 哈希一致
    canon = json.dumps(TOOLS["permissions"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    expect = "sha256:" + hashlib.sha256(canon.encode("utf-8")).hexdigest()
    assert sig["permissions_digest"] == expect


def test_signature_shell_with_real_digest(tmp_path):
    """W4-plan 4.1 验收补强：signature 空壳存在且 permissions_digest 真实写入。"""
    pkg = export_package(_spec(), tmp_path)
    with zipfile.ZipFile(pkg) as zf:
        sig = json.loads(zf.read("signature.json"))
        manifest = json.loads(zf.read("manifest.json"))
    assert sig["permissions_digest"] != "sha256:"  # 非空壳值
    assert "hash" in manifest and manifest["hash"]["value"] != ""


def test_high_risk_skill_without_confirm_rejected(tmp_path):
    bad = _spec(skills={"danger": "name: danger\nrisk_level: high\nrequires_confirm: false\n"})
    with pytest.raises(PackageExportError, match="requires_confirm"):
        export_package(bad, tmp_path)


def test_non_allowlisted_tool_rejected(tmp_path):
    tools = json.loads(json.dumps(TOOLS))
    tools["tools"] = [{"name": "evil_tool", "source": "builtin"}]
    with pytest.raises(PackageExportError, match="白名单"):
        export_package(_spec(tools=tools), tmp_path)


def test_aggregate_hash_excludes_self_references(tmp_path):
    """manifest/signature 排除在聚合哈希外，避免自引用。"""
    b = tmp_path / "b"
    b.mkdir()
    (b / "prompt.md").write_text("x", encoding="utf-8")
    (b / "signature.json").write_text("sig", encoding="utf-8")
    (b / "manifest.json").write_text("man", encoding="utf-8")
    h = compute_aggregate_hash(b)
    expect = hashlib.sha256(b"prompt.mdx").hexdigest()
    assert h == expect
