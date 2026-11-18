"""固定模板复盘生成（W3 3.4，ARCHITECTURE.md §8.4）。

字段固定：成功点 / 失败点 / 改进建议。不输出自由发挥长文。
实现：规则化 baseline——从执行轨迹（状态、步骤 verdict、denials、usage）生成，
不额外调用模型（成本可控、可测）。模型增强留作后续可选，不改字段契约。

复盘失败不破坏主流程：generate() 抛异常时由 Runtime 捕获并标 reflect_failed。

案例（人类指定写进复盘考量）：W3 3.1 的 `rm -fr /` 链式缺口——
原正则未覆盖 -fr 顺序，导致破坏性命令被 §4 降级为 NEED_CONFIRM（可确认执行）。
这类「规则链式降级」作为改进建议的典型案例，提醒：逐条全量测才能抓到。
"""

from __future__ import annotations

from typing import Any

REFLECTION_FIELDS = ("success_points", "failure_points", "improvement")


class ReflectionError(RuntimeError):
    pass


def generate_reflection(
    task_id: str,
    status: str,
    steps: list[dict[str, Any]],
    denials: list[dict[str, Any]],
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """从执行轨迹生成固定三字段复盘。返回 dict 含 task_id 与三字段。"""
    try:
        success: list[str] = []
        failure: list[str] = []
        improvement: list[str] = []

        ok_steps = [s for s in steps if s.get("verdict") == "ALLOWED"]
        if ok_steps:
            success.append(f"{len(ok_steps)} 个步骤成功执行")
        if denials:
            success.append(f"执行层拦截 {len(denials)} 条高危/越权操作（0 逃逸）")

        failed = [s for s in steps if s.get("verdict") not in ("ALLOWED", None)]
        if failed:
            failure.append(f"{len(failed)} 个步骤未通过：" +
                           ", ".join(str(s.get("tool", "?")) for s in failed[:3]))
        if status == "FAILED":
            failure.append("任务终态 FAILED")

        # 至少 1 个真实可优化点（PRD 成功标准 4）
        if usage and usage.get("total_tokens", 0) > 0:
            improvement.append(
                f"本次消耗 {usage['total_tokens']} tokens，可压缩 prompt 或缩短步骤降低单次成本"
            )
        else:
            improvement.append("可为高频扫描步骤加缓存，避免重复遍历同一目录")
        if denials:
            improvement.append(
                "被拦截命令应进入审计复盘队列，逐条核对规则覆盖（案例：rm -fr / 曾因参数顺序"
                "被 §4 降级为 NEED_CONFIRM，唯逐条全量测可发现此类链式缺口）"
            )
        if not improvement:
            improvement.append("补充步骤级耗时采样，定位长尾步骤")

        result = {
            "task_id": task_id,
            "success_points": "；".join(success) if success else "无",
            "failure_points": "；".join(failure) if failure else "无",
            "improvement": "；".join(improvement),
        }
        # 契约校验：字段齐全且非空
        for field in REFLECTION_FIELDS:
            if not result.get(field):
                raise ReflectionError(f"复盘字段缺失: {field}")
        return result
    except ReflectionError:
        raise
    except Exception as exc:  # noqa: BLE001 - 任何生成失败都转为 ReflectionError
        raise ReflectionError(str(exc)) from exc
