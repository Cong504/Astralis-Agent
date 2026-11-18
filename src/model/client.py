"""模型客户端：生成计划 → 归一化 → 校验 → 失败重试 1 次。

架构落点：W1-plan.md §1.5、ADR-0003（云端先行，不做自动互备）。
真实云端 A1' 发现：DeepSeek 原始输出 tool 名/step_id 类型不守 Schema，
故 Model 层先 normalize_plan 归一到固定 Schema（不放宽 Schema 本身），
Runtime 仍只拿到合法 JSON。usage 由 RouteResult 上报，累计预算归 Runtime。
"""

from __future__ import annotations

from typing import Any

from .parser import normalize_plan
from .router import ModelRouter, RouteResult


def generate_plan_with_retry(router: ModelRouter, prompt: str, max_retry: int = 1) -> tuple[dict[str, Any], RouteResult]:
    """调用路由生成并归一化为合法计划，返回 (plan, route_result)。

    重试与错误语义由 ModelRouter.generate 统一处理；本层提供归一化 parse_fn。
    """
    route = router.generate(prompt, parse_fn=lambda raw: normalize_plan(raw)[0], max_retry=max_retry)
    # router 已用同一 parse_fn 校验通过；再取一次以保留归一化报告
    normalized, report = normalize_plan(route.text)
    route.normalization = report
    return normalized, route
