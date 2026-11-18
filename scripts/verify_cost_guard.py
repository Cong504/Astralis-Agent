"""3.6 成本护栏真实验证（不新烧 API 配额）。

用 A1' 已落盘的真实 usage 样本（DeepSeek 真实返回的 prompt/completion tokens）
喂给 Runtime TokenBudget，验证：
  1. 正常阈值（config.json 默认 10000 tokens）下 10 次真实 usage 累计不超限；
  2. 低阈值下真实 usage 触发超限，抛 TokenBudgetExceeded，结构化告警含维度。

数据来自 logs/bench_a1_legality.json（A1' 10 次真实调用原始样本），不新发 API 调用。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from compat.encoding import force_utf8_stdio  # noqa: E402

force_utf8_stdio()  # 全局 UTF-8 防御取代原个案 reconfigure（W3 复盘系统级方案）

from model.router import TokenUsage  # noqa: E402
from runtime.token_budget import (  # noqa: E402
    TokenBudget, TokenBudgetConfig, TokenBudgetExceeded,
)


def main() -> int:
    path = ROOT / "logs" / "bench_a1_legality.json"
    if not path.exists():
        print("[cost] ERROR: 缺 A1' 真实样本 logs/bench_a1_legality.json，先跑 bench_a1_legality.py")
        return 2
    data = json.loads(path.read_text(encoding="utf-8"))
    samples = [s for s in data["samples"] if "prompt_tokens" in s]
    if not samples:
        print("[cost] ERROR: 样本无 usage 字段")
        return 2

    # 1. 默认阈值：真实 usage 累计不超限
    budget = TokenBudget(TokenBudgetConfig.from_file(), task_id="cost-verify")
    for s in samples:
        budget.consume(TokenUsage(s["prompt_tokens"], s["completion_tokens"]))
    snap = budget.snapshot()
    ok_normal = snap["calls"] == len(samples)
    print(f"[cost] 默认阈值：{len(samples)} 次真实 usage 累计 {snap['total_tokens']} tokens，"
          f"CNY {snap['estimated_cost_cny']:.4f}，未超限 → {'PASS' if ok_normal else 'FAIL'}")

    # 2. 低阈值：真实 usage 触发超限 + 结构化告警
    low = TokenBudget(TokenBudgetConfig(max_tokens=100, max_cost_cny=99.0), task_id="cost-low")
    triggered = None
    for s in samples:
        try:
            low.consume(TokenUsage(s["prompt_tokens"], s["completion_tokens"]))
        except TokenBudgetExceeded as exc:
            triggered = exc.to_alert()
            break
    ok_low = triggered is not None and triggered["dimension"] in ("tokens", "cost") \
        and "snapshot" in triggered
    print(f"[cost] 低阈值：真实 usage 触发超限 → {triggered['message'] if triggered else '未触发'}")
    print(f"[cost] 结构化告警维度={triggered['dimension'] if triggered else 'N/A'} "
          f"→ {'PASS' if ok_low else 'FAIL'}")

    ok = ok_normal and ok_low
    print(f"[cost] {'ALL PASS' if ok else 'HAS FAIL'}（数据源：A1' 真实 API usage，未新烧配额）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
