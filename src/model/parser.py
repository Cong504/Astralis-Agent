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
