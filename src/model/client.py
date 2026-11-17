"""模型客户端：生成计划 → 解析 → 失败重试 1 次 → 仍失败交路由降级。

架构落点：W1-plan.md §1.5「非法 JSON 捕获 → 触发 1 次重试 → 降级」。
"""

from __future__ import annotations

from typing import Any

from .parser import PlanParseError, parse_plan
from .router import Backend, ModelRouter, RouteResult


def generate_plan_with_retry(router: ModelRouter, prompt: str, max_retry: int = 1) -> tuple[dict[str, Any], RouteResult]:
    """按后端逐一尝试；每个后端内部对解析失败重试 max_retry 次。

    返回 (plan, route_result)。全部失败抛 PlanParseError。
    """
    backends = [router.primary, router.fallback]
    attempts: list[str] = []
    last_exc: Exception | None = None

    for backend in backends:
        for attempt in range(max_retry + 1):
            try:
                text = backend.generate(prompt)
                plan = parse_plan(text)
                rr = RouteResult(text, backend.name, backend is router.fallback, attempts + [backend.name])
                return plan, rr
            except Exception as exc:  # noqa: BLE001 - 抓解析与后端错误
                last_exc = exc
                attempts.append(f"{backend.name}#{attempt}: {type(exc).__name__}")
                if isinstance(exc, PlanParseError) and attempt < max_retry:
                    continue
                break  # 该后端耗尽，切下一个
    raise PlanParseError(f"全部后端失败: {attempts}") from last_exc
