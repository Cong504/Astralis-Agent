"""模型输出解析与校验层。

架构落点：ARCHITECTURE.md §8.1「模型层必须输出结构化 JSON，解析失败即视为模型失败」。
职责：把模型返回的原始文本安全解析为计划 dict，并由 Schema 校验；不执行任何操作。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator

_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "plan.schema.json"


class PlanParseError(ValueError):
    """模型输出无法解析为合法计划。"""


def load_validator() -> Draft7Validator:
    with _SCHEMA_PATH.open(encoding="utf-8") as fh:
        schema = json.load(fh)
    Draft7Validator.check_schema(schema)
    return Draft7Validator(schema)


def parse_plan(raw: str, validator: Draft7Validator | None = None) -> dict[str, Any]:
    """解析模型输出文本，返回校验通过的计划 dict。

    失败时抛 PlanParseError：JSON 非法、非对象、或字段不满足 Schema。
    """
    if validator is None:
        validator = load_validator()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise PlanParseError(f"JSON 解析失败: {exc}") from exc
    if not isinstance(data, dict):
        raise PlanParseError("模型输出顶层必须是 JSON 对象")
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
    if errors:
        first = errors[0]
        loc = ".".join(str(p) for p in first.path) or "<root>"
        raise PlanParseError(f"Schema 校验失败 @{loc}: {first.message}")
    return data


# 真实云端（DeepSeek）A1' 实测：模型会用自由 tool 名（如 filesystem_scan/none）与数字 step_id。
# 归一化在 Model 层完成，产物仍是固定 Schema 的合法 JSON；不放宽 Schema 本身。
TOOL_ALIASES: dict[str, str] = {
    "filesystem_scan": "dir_scan", "list_directory": "dir_scan", "ls": "dir_scan",
    "read_file": "file_read", "cat": "file_read",
    "write_file": "report_write", "write_report": "report_write",
    "run_script": "script_run", "exec": "script_run", "bash": "script_run",
    "none": "log_record", "noop": "log_record",
}

_ALLOWED_TOOLS = ("dir_scan", "file_read", "script_run", "report_write", "log_record")
_ALLOWED_STEP_KEYS = {"step_id", "action", "tool", "args", "risk_level", "requires_confirm"}


def normalize_plan(raw: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """把模型自由输出归一到固定 Schema，返回 (归一化计划, 归一化报告)。

    报告字段供 A1' 统计：原始是否合法、做了哪些改写。归一后仍需过 parse_plan 校验。
    """
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise PlanParseError("模型输出顶层必须是 JSON 对象")
    report: dict[str, Any] = {"raw_valid": False, "rewrites": []}

    steps = data.get("steps")
    if not isinstance(steps, list):
        raise PlanParseError("steps 必须为数组")
    normalized_steps = []
    for i, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            raise PlanParseError(f"steps[{i-1}] 必须是对象")
        s = {k: v for k, v in step.items() if k in _ALLOWED_STEP_KEYS}
        sid = s.get("step_id")
        if not isinstance(sid, str) or not sid:
            s["step_id"] = f"step-{i}"
            report["rewrites"].append(f"step[{i-1}].step_id -> {s['step_id']}")
        tool = str(s.get("tool", "")).strip().lower()
        if tool in TOOL_ALIASES:
            s["tool"] = TOOL_ALIASES[tool]
            report["rewrites"].append(f"step[{i-1}].tool {tool} -> {s['tool']}")
        elif tool not in _ALLOWED_TOOLS:
            # 未知 tool 一律降级为无副作用的 log_record，守住白名单硬约束；
            # 不放宽 Schema，也不让幻觉工具名进执行层。诚实记入 A1' 报告。
            s["tool"] = "log_record"
            report["rewrites"].append(f"step[{i-1}].tool 未知({tool}) -> log_record[安全降级]")
        s.setdefault("args", {})
        if not isinstance(s.get("args"), dict):
            s["args"] = {}
            report["rewrites"].append(f"step[{i-1}].args -> {{}}")
        if s.get("risk_level") not in ("low", "medium", "high"):
            s["risk_level"] = "low"
            report["rewrites"].append(f"step[{i-1}].risk_level -> low")
        if s["risk_level"] == "high":
            s["requires_confirm"] = True
        normalized_steps.append(s)

    normalized: dict[str, Any] = {
        "task_id": str(data.get("task_id", "")),
        "goal": str(data.get("goal", "")),
        "steps": normalized_steps,
    }
    if not normalized["task_id"] or not normalized["goal"]:
        raise PlanParseError("task_id / goal 缺失")
    # 用原 Schema 校验归一结果；通过才算可用
    parse_plan(json.dumps(normalized, ensure_ascii=False))
    report["raw_valid"] = not report["rewrites"]
    return normalized, report
