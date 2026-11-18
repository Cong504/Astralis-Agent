"""Runtime 层 Token 预算护栏测试（ADR-0003）。"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from model.router import TokenUsage  # noqa: E402
from runtime.token_budget import (  # noqa: E402
    TokenBudget, TokenBudgetConfig, TokenBudgetExceeded,
)
from runtime.task_runner import RuntimeUI, run_task  # noqa: E402
from data.db import TaskLogger  # noqa: E402
from data.events import EventLog  # noqa: E402
from model.router import build_stub_router  # noqa: E402


def test_budget_accumulates_across_calls():
    cfg = TokenBudgetConfig(max_tokens=10000, max_cost_cny=99.0, price_per_1k_tokens_cny=0.001)
    budget = TokenBudget(cfg, task_id="t-1")
    budget.consume(TokenUsage(100, 50))
    budget.consume(TokenUsage(200, 80))
    snap = budget.snapshot()
    assert snap["total_tokens"] == 430
    assert snap["calls"] == 2


def test_budget_exceeded_by_tokens():
    cfg = TokenBudgetConfig(max_tokens=100, max_cost_cny=99.0)
    budget = TokenBudget(cfg)
    budget.consume(TokenUsage(60, 10))
    with pytest.raises(TokenBudgetExceeded) as e:
        budget.consume(TokenUsage(50, 0))
    assert e.value.snapshot["total_tokens"] == 120


def test_budget_exceeded_by_cost():
    cfg = TokenBudgetConfig(max_tokens=999999, max_cost_cny=0.001, price_per_1k_tokens_cny=1.0)
    budget = TokenBudget(cfg)
    with pytest.raises(TokenBudgetExceeded):
        budget.consume(TokenUsage(10, 0))


def test_config_from_file():
    cfg = TokenBudgetConfig.from_file()
    assert cfg.max_tokens == 10000
    assert cfg.max_cost_cny == 0.5


def test_runtime_aborts_on_budget_exceeded(tmp_path):
    """Runtime 状态机在预算超限时中止任务、告警、写 FAILED，不进入确认阶段。"""
    # 极端阈值：stub 的 usage=0 也会因 cost 单价放大？stub 为 0，改用一个注入 usage 的后端
    class FakeUsageBackend:
        name = "fake"
        last_usage = TokenUsage(20000, 0)

        def generate(self, prompt):
            import json
            return json.dumps({
                "task_id": "x", "goal": "g",
                "steps": [{"step_id": "step-1", "action": "a", "tool": "dir_scan",
                           "args": {}, "risk_level": "low"}],
            }, ensure_ascii=False)

    from model.router import ModelRouter
    router = ModelRouter(primary=FakeUsageBackend())
    dbp = tmp_path / "agent.db"
    ep = tmp_path / "events.jsonl"
    alerts = []
    ui = RuntimeUI(alert=lambda msg: alerts.append(msg))

    tid = run_task(
        "测试中止", router=router,
        db=TaskLogger(dbp), events=EventLog(ep),
        ui=ui,
        budget_config=TokenBudgetConfig(max_tokens=100, max_cost_cny=99.0),
    )
    with TaskLogger(dbp) as db:
        r = db.replay(tid)
        assert r["task"]["status"] == "FAILED"
    assert alerts and "中止" in alerts[0]
    states = [e["state"] for e in EventLog(ep).for_task(tid)]
    assert "AWAIT_CONFIRM" not in states
    assert states[-1] == "FAILED"
