"""A1' 合法率压测（W2 最后一块真实拼图，人类裁决 2026-11-18）。

目标：DeepSeek 10 次规划，记两个独立指标，防单点正面证据掩盖方差：
  1. raw_valid 比例：原始输出不归一化、直接过 plan.schema.json 校验的次数占比。
  2. normalize 降级次数：原始不合法、经 Model 层 normalize_plan 改写后才可用的次数。
     每次记录触发了哪些改写（数字 step_id / 自由 tool 名 / 未知字段等）。

附带：每次延迟、token usage、原始文本样本（落盘 JSON）。
判定参考：PRD 成功标准 6「10 次 ≥8 次合法」——分别给 raw 口径与归一化后口径，
不擅自合并；由人类据原始数据判 W2 是否可收口。

用法：python scripts/bench_a1_legality.py
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from model.parser import load_validator, normalize_plan, parse_plan  # noqa: E402
from model.router import BackendError, build_cloud_router  # noqa: E402
from runtime.task_runner import PLANNING_PROMPT  # noqa: E402

N = 10
TASKS = [
    "统计指定目录下的文件数量与类型分布",
    "扫描工作目录并生成 Markdown 报告",
    "读取某个文本文件并统计行数",
    "列出最近修改的文件",
    "统计代码中 TODO 注释数量",
    "检查磁盘占用最大的文件",
    "对比两个目录的文件差异",
    "查找重复文件并列出路径",
    "统计日志文件中 ERROR 出现次数",
    "归类文件并生成清单",
]


def p95(values: list[float]) -> float:
    if not values:
        return float("inf")
    ordered = sorted(values)
    return ordered[max(0, int(round(0.95 * (len(ordered) - 1))))]


def main() -> int:
    router = build_cloud_router()
    backend = router.primary
    print(f"[A1'] backend={backend.name}（{'真实云端' if backend.name != 'stub' else 'stub 桩'}）")
    if backend.name == "stub":
        print("[A1'] ERROR: 无云端 Key，无法做真实合法率测试；本脚本拒绝用 stub 数据冒充")
        return 2

    validator = load_validator()
    prompt_tmpl = (PLANNING_PROMPT
                   .replace("TASK_ID_PLACEHOLDER", "a1-{i}")
                   .replace("RAW_INPUT_PLACEHOLDER", "{task}"))
    records: list[dict] = []
    latencies: list[float] = []

    for i, task in enumerate(TASKS, 1):
        prompt = prompt_tmpl.replace("{i}", str(i)).replace("{task}", task)
        t0 = time.perf_counter()
        try:
            raw = backend.generate(prompt)
        except BackendError as exc:
            records.append({"i": i, "task": task, "error": str(exc)})
            continue
        latency = time.perf_counter() - t0
        latencies.append(latency)
        usage = backend.last_usage

        rec: dict = {
            "i": i, "task": task, "latency_sec": round(latency, 3),
            "prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens,
            "raw": raw,
        }
        # 指标 1：原始输出直接过 Schema
        try:
            parse_plan(raw, validator)
            rec["raw_valid"] = True
            rec["raw_error"] = ""
        except Exception as exc:  # noqa: BLE001 - 记录任何原始非法原因
            rec["raw_valid"] = False
            rec["raw_error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
        # 指标 2：归一化后是否可用 + 改写明细
        try:
            plan, report = normalize_plan(raw)
            rec["normalized_ok"] = True
            rec["rewrites"] = report["rewrites"]
            rec["degraded"] = bool(report["rewrites"])
            rec["steps"] = len(plan["steps"])
        except Exception as exc:  # noqa: BLE001
            rec["normalized_ok"] = False
            rec["rewrites"] = []
            rec["degraded"] = False
            rec["normalize_error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
        records.append(rec)
        print(f"[A1'] #{i:02d} raw_valid={rec['raw_valid']} degraded={rec.get('degraded')} "
              f"lat={latency:.2f}s rewrites={len(rec.get('rewrites', []))}")

    valid_records = [r for r in records if "raw_valid" in r]
    raw_valid_n = sum(1 for r in valid_records if r["raw_valid"])
    normalized_ok_n = sum(1 for r in valid_records if r.get("normalized_ok"))
    degraded_n = sum(1 for r in valid_records if r.get("degraded"))
    total = len(valid_records)
    pt = sum(r.get("prompt_tokens", 0) for r in records)
    ct = sum(r.get("completion_tokens", 0) for r in records)

    print("\n[A1'] === 汇总 ===")
    print(f"[A1'] 有效样本 {total}/{N}，API 错误 {N-total}")
    print(f"[A1'] raw_valid（原始合法率）= {raw_valid_n}/{total} ({raw_valid_n/total:.0%})" if total else "")
    print(f"[A1'] normalized_ok（归一化后可用）= {normalized_ok_n}/{total} ({normalized_ok_n/total:.0%})" if total else "")
    print(f"[A1'] normalize 降级次数 = {degraded_n}/{total}")
    if latencies:
        print(f"[A1'] 延迟 p95={p95(latencies):.2f}s 中位数={statistics.median(latencies):.2f}s "
              f"max={max(latencies):.2f}s")
    print(f"[A1'] tokens: prompt={pt} completion={ct} 合计={pt+ct}")

    # 降级原因归类
    reasons: dict[str, int] = {}
    for r in valid_records:
        for rw in r.get("rewrites", []):
            key = rw.split("->")[0].strip()
            reasons[key] = reasons.get(key, 0) + 1
    print(f"[A1'] 降级原因分布: {json.dumps(reasons, ensure_ascii=False)}")

    out = ROOT / "logs" / "bench_a1_legality.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({
        "backend": backend.name,
        "n_tasks": N,
        "raw_valid_n": raw_valid_n,
        "normalized_ok_n": normalized_ok_n,
        "degraded_n": degraded_n,
        "latency_p95_sec": p95(latencies) if latencies else None,
        "tokens": {"prompt": pt, "completion": ct, "total": pt + ct},
        "degrade_reasons": reasons,
        "samples": records,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[A1'] 原始样本已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
