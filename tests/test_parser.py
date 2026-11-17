"""1.5 解析校验层单元测试：合法通过、非法 JSON 拒绝、字段违规拒绝。"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from model.parser import PlanParseError, load_validator, parse_plan  # noqa: E402

VALIDATOR = load_validator()

VALID_PLAN = {
    "task_id": "t-001",
    "goal": "统计指定目录下的文件数量与类型分布",
    "steps": [
        {
            "step_id": "step-1",
            "action": "扫描目录并统计",
            "tool": "dir_scan",
            "args": {"path": "/workspace/data"},
            "risk_level": "low",
        }
    ],
}


def test_valid_plan_passes():
    assert parse_plan(json.dumps(VALID_PLAN), VALIDATOR) == VALID_PLAN


def test_illegal_json_rejected():
    with pytest.raises(PlanParseError, match="JSON 解析失败"):
        parse_plan("this is not json", VALIDATOR)


def test_non_object_rejected():
    with pytest.raises(PlanParseError, match="顶层必须是 JSON 对象"):
        parse_plan("[1,2,3]", VALIDATOR)


def test_missing_required_field_rejected():
    bad = json.loads(json.dumps(VALID_PLAN))
    del bad["steps"][0]["tool"]
    with pytest.raises(PlanParseError, match="Schema 校验失败"):
        parse_plan(json.dumps(bad), VALIDATOR)


def test_high_risk_requires_confirm():
    bad = json.loads(json.dumps(VALID_PLAN))
    bad["steps"][0]["risk_level"] = "high"
    with pytest.raises(PlanParseError, match="Schema 校验失败"):
        parse_plan(json.dumps(bad), VALIDATOR)
    bad["steps"][0]["requires_confirm"] = True
    assert parse_plan(json.dumps(bad), VALIDATOR)["steps"][0]["requires_confirm"] is True
