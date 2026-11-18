"""模型客户端：生成计划 → 解析 → 解析失败重试 1 次 → 仍失败报错。

架构落点：W1-plan.md §1.5、ADR-0003（云端先行，不做自动互备）。
usage 由 Model 层随 RouteResult 上报，累计预算由 Runtime 层负责。
"""

from __future__ import annotations

from typing import Any

from .parser import parse_plan
from .router import ModelRouter, RouteResult


def generate_plan_with_retry(router: ModelRouter, prompt: str, max_retry: int = 1) -> tuple[dict[str, Any], RouteResult]:
    """调用路由生成并校验计划，返回 (plan, route_result)。

    重试与错误语义由 ModelRouter.generate 统一处理；本层只做解析入口。
    """
    route = router.generate(prompt, parse_fn=parse_plan, max_retry=max_retry)
    plan = parse_plan(route.text)
    return plan, route
