"""Agent 包导出（W4 4.1，PACKAGE_SPEC.md §2/§3/§9/§10）。

7 部分结构：manifest.json / prompt.md / skills/ / tools.json / config.json /
memory/ / signature.json。hash.value 排除 manifest 与 signature（避免自引用）；
signature.permissions_digest 对 tools.json.permissions 真实计算（v0.1 空壳但字段实算）。

导出失败抛 PackageExportError，说明具体步骤/文件/字段，不静默产坏包。
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

SPEC_VERSION = "0.1"
MIN_RUNTIME = "0.1.0"
ALLOWED_TOOLS = ("dir_scan", "file_read", "script_run", "report_write", "log_record")

# 聚合哈希排除集合（往返契约：4.1 export 与 4.2 import 必须用同一集合）。
# PACKAGE_SPEC.md §3/§10 字面只排除 signature.json；但 manifest.json 内含 hash.value 自身，
# 若计入则自引用无解。故权威定义 = 排除 manifest.json + signature.json，已回写 spec 澄清。
HASH_EXCLUDED = frozenset({"manifest.json", "signature.json"})


class PackageExportError(ValueError):
    pass


@dataclass
class AgentSpec:
    """待导出 Agent 的七部分原料。"""
    name: str
    prompt: str
    tools: dict[str, Any]              # 含 name/protocol/permissions
    config: dict[str, Any] = field(default_factory=dict)
    skills: dict[str, str] = field(default_factory=dict)   # skill_name -> yaml 文本
    memory: dict[str, Any] = field(default_factory=lambda: {"schema": "experience.v1", "items": []})
    version: str = "1.0.0"
    author: str = "local"
    description: str = ""


def _canon(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_permissions_digest(tools: dict[str, Any]) -> str:
    """sha256:<tools.json.permissions 的规范化哈希>（PACKAGE_SPEC §9，v0.1 真实计算）。"""
    perms = tools.get("permissions", {})
    return f"sha256:{_sha256_text(_canon(perms))}"


def compute_aggregate_hash(build_dir: Path, excluded: frozenset[str] = HASH_EXCLUDED) -> str:
    """对排除集合外的全部文件，按相对路径排序聚合 sha256（4.1/4.2 共用同一 excluded）。"""
    h = hashlib.sha256()
    files = sorted(
        (p for p in build_dir.rglob("*") if p.is_file() and p.relative_to(build_dir).as_posix() not in excluded),
        key=lambda p: p.relative_to(build_dir).as_posix(),
    )
    if not files:
        raise PackageExportError("聚合哈希失败：无可用文件")
    for path in files:
        rel = path.relative_to(build_dir).as_posix()
        h.update(rel.encode("utf-8"))
        h.update(path.read_bytes())
    return h.hexdigest()


def _as_bool(value: Any) -> bool:
    """把 yaml 解析出的布尔/字符串统一为 bool（兼容 true/True/yes/on/1 及带引号写法）。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "on", "1")
    if isinstance(value, (int, float)):
        return value != 0
    return False


def validate_structure(tools: dict[str, Any], skills: dict[str, str]) -> None:
    """结构校验（PACKAGE_SPEC §10 step5）：JSON 字段、risk_level 与 requires_confirm 一致。

    用 yaml.safe_load 解析后按布尔/枚举判断，不做子串匹配——
    避免 True/yes/紧凑写法被误拒、带引号写法被误放（人类裁决 2026-11-19）。
    """
    for skill_name, yaml_text in skills.items():
        try:
            data = yaml.safe_load(yaml_text) or {}
        except yaml.YAMLError as exc:
            raise PackageExportError(f"结构校验失败：skill {skill_name} YAML 解析错误: {exc}") from exc
        if not isinstance(data, dict):
            raise PackageExportError(f"结构校验失败：skill {skill_name} YAML 顶层必须是对象")
        risk = str(data.get("risk_level", "")).strip().lower()
        if risk not in ("low", "medium", "high"):
            raise PackageExportError(f"结构校验失败：skill {skill_name} risk_level 非法: {risk}")
        if risk == "high" and not _as_bool(data.get("requires_confirm")):
            raise PackageExportError(
                f"结构校验失败：skill {skill_name} risk_level=high 但 requires_confirm 非 true（PACKAGE_SPEC §5）"
            )
    for tool in tools.get("tools", []):
        if tool.get("name") not in ALLOWED_TOOLS:
            raise PackageExportError(f"结构校验失败：工具不在白名单: {tool.get('name')}")


def export_package(spec: AgentSpec, out_dir: Path | str) -> Path:
    """导出 <name>-v<version>.agentpkg 到 out_dir，返回包路径。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    if not spec.name or not spec.prompt:
        raise PackageExportError("导出失败：name / prompt 不能为空")

    with tempfile.TemporaryDirectory(prefix="agentpkg-") as tmp:
        build = Path(tmp)
        (build / "skills").mkdir()
        (build / "memory").mkdir()

        # 逐文件写入，失败报具体文件
        try:
            (build / "prompt.md").write_text(spec.prompt, encoding="utf-8")
            (build / "tools.json").write_text(_canon(spec.tools), encoding="utf-8")
            (build / "config.json").write_text(_canon(spec.config), encoding="utf-8")
            (build / "memory" / "experiences.json").write_text(_canon(spec.memory), encoding="utf-8")
            for name, text in spec.skills.items():
                (build / "skills" / f"{name}.yaml").write_text(text, encoding="utf-8")
        except OSError as exc:
            raise PackageExportError(f"写入文件失败: {exc}") from exc

        validate_structure(spec.tools, spec.skills)

        digest = compute_permissions_digest(spec.tools)
        signature = {
            "algorithm": None, "public_key": None, "signature": None,
            "signed_at": None, "signer": None,
            "permissions_digest": digest,
            "trust": "local-unverified",
            "notes": "v0.1 不实现签名逻辑，仅预留字段与校验入口",
        }
        (build / "signature.json").write_text(_canon(signature), encoding="utf-8")

        agg = compute_aggregate_hash(build)
        manifest = {
            "spec_version": SPEC_VERSION,
            "name": spec.name,
            "version": spec.version,
            "author": spec.author,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "description": spec.description,
            "hash": {"algorithm": "sha256", "value": agg},
            "entrypoint": "prompt.md",
            "min_runtime": MIN_RUNTIME,
            "capabilities_required": [t.get("name") for t in spec.tools.get("tools", [])],
        }
        (build / "manifest.json").write_text(_canon(manifest), encoding="utf-8")

        pkg = out / f"{spec.name}-v{spec.version}.agentpkg"
        try:
            with zipfile.ZipFile(pkg, "w", zipfile.ZIP_DEFLATED) as zf:
                for path in sorted(build.rglob("*")):
                    if path.is_file():
                        zf.write(path, path.relative_to(build).as_posix())
        except OSError as exc:
            raise PackageExportError(f"压缩失败: {exc}") from exc
        return pkg
