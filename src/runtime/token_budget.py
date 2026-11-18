"""Runtime 层 Token 预算护栏（ADR-0003，W1.8）。

职责边界：
  - Model 层只在 RouteResult 上报单次调用的 usage，不累计、不判断。
  - Runtime 维护 task_token_budget，跨一个任务的多次模型调用累计；超阈值抛
    TokenBudgetExceeded，由 Runtime 状态机中止任务并告警。

阈值来源：config.json 的 runtime.token_budget（默认 10000 tokens 或 ¥0.5）。
任一阈值超限即中止（tokens 或 cost）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from model.router import TokenUsage

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.json"


@dataclass(frozen=True)
class TokenBudgetConfig:
    max_tokens: int = 10000
    max_cost_cny: float = 0.5
    price_per_1k_tokens_cny: float = 0.001

    @classmethod
    def from_file(cls, path: Path | str | None = None) -> "TokenBudgetConfig":
        p = Path(path) if path else _CONFIG_PATH
        if not p.exists():
            return cls()
        try:
            with p.open(encoding="utf-8") as fh:
                raw = json.load(fh).get("runtime", {}).get("token_budget", {})
        except (OSError, json.JSONDecodeError):
            return cls()
        return cls(
            max_tokens=int(raw.get("max_tokens", cls.max_tokens)),
            max_cost_cny=float(raw.get("max_cost_cny", cls.max_cost_cny)),
            price_per_1k_tokens_cny=float(raw.get("price_per_1k_tokens_cny", cls.price_per_1k_tokens_cny)),
        )


class TokenBudgetExceeded(RuntimeError):
    """任务 token 预算超限，Runtime 必须中止任务并告警。"""

    def __init__(self, message: str, snapshot: dict):
        super().__init__(message)
        self.snapshot = snapshot


class TokenBudget:
    """单个任务的累计预算账本，由 Runtime 持有，一次任务一个实例。"""

    def __init__(self, config: TokenBudgetConfig | None = None, task_id: str = ""):
        self.config = config or TokenBudgetConfig.from_file()
        self.task_id = task_id
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.calls = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def estimated_cost_cny(self) -> float:
        return self.total_tokens / 1000.0 * self.config.price_per_1k_tokens_cny

    def snapshot(self) -> dict:
        return {
            "task_id": self.task_id,
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "max_tokens": self.config.max_tokens,
            "estimated_cost_cny": round(self.estimated_cost_cny, 4),
            "max_cost_cny": self.config.max_cost_cny,
        }

    def consume(self, usage: TokenUsage) -> dict:
        """累计一次调用 usage；超阈值抛 TokenBudgetExceeded（不自行吞掉）。"""
        self.calls += 1
        self.prompt_tokens += usage.prompt_tokens
        self.completion_tokens += usage.completion_tokens
        snap = self.snapshot()
        if self.total_tokens >= self.config.max_tokens:
            raise TokenBudgetExceeded(
                f"Token 预算超限: {self.total_tokens} >= {self.config.max_tokens} tokens", snap,
            )
        if self.estimated_cost_cny >= self.config.max_cost_cny:
            raise TokenBudgetExceeded(
                f"成本预算超限: ¥{self.estimated_cost_cny:.4f} >= ¥{self.config.max_cost_cny}", snap,
            )
        return snap
