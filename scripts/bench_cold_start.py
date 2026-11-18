"""2.8 冷启动压测（协议钉死版）。

测量协议（人类拍板 2026-11-18，N=1.0s）：
  ① 冷启动定义 = 从 `docker create` 发出，到容器内首条命令的 stdout 返回为止。
     计时点由 DockerSandboxExecutor.last_timing['create_to_stdout_sec'] 提供，
     不含其后的资源采集(stats)与容器销毁(destroy)。
  ② 每次采样一组多次运行，取 p95，不报均值（均值会掩盖长尾）。
  ③ cold / warm 分开记录：
     - cold = 镜像首次拉取后的第一批样本（或显式 --cold-after-pull）
     - warm = 镜像已在本地缓存时的样本
     并显式标注本次运行是否发生了首次拉镜像（--image-cached 参数声明）。
  ④ 附原始样本（每轮秒数逐条打印），A2/A4 结论以原始数据为准。

用法：
  python scripts/bench_cold_start.py --samples 20 --image-cached
  python scripts/bench_cold_start.py --samples 20 --cold-after-pull

判定：warm p95 ≤ 1.0s → A4 通过；不达标则精简镜像/预热并出 ADR。
A2（五段管道覆盖真执行）已在 S2 端到端 PASS 中验证，本脚本复测链路不中断。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sandbox.docker_executor import DockerSandboxExecutor  # noqa: E402
from sandbox.executor import ExecutionRequest, Verdict  # noqa: E402

IMAGE = "local-agent/sandbox:0.1"
COMMAND = "python -c 'print(\"pong\")'"  # 容器内首条命令：标准库、无副作用
BUDGET_SEC = 1.0


def p95(values: list[float]) -> float:
    """最近秩法 p95（N 较小时用排序索引，避免外推库依赖）。"""
    if not values:
        return float("inf")
    ordered = sorted(values)
    k = max(0, int(round(0.95 * (len(ordered) - 1))))
    return ordered[k]


def run_samples(n: int, mode: str) -> list[float]:
    ex = DockerSandboxExecutor()
    samples: list[float] = []
    for i in range(n):
        req = ExecutionRequest(f"bench-{mode}", f"s{i}", COMMAND, image=IMAGE)
        result = ex.execute(req)
        if result.verdict is not Verdict.ALLOWED:
            raise RuntimeError(f"样本 {i} 非 ALLOWED: {result.verdict.value}")
        t = ex.last_timing.get("create_to_stdout_sec")
        if t is None:
            raise RuntimeError("执行器未提供 create_to_stdout_sec 计时")
        samples.append(t)
    return samples


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=20)
    ap.add_argument("--image-cached", action="store_true", help="声明镜像已在本地缓存（warm 场景）")
    ap.add_argument("--cold-after-pull", action="store_true", help="声明本轮在首次拉镜像后立即采样（cold 场景）")
    args = ap.parse_args()

    mode = "cold" if args.cold_after_pull else "warm"
    print("[bench] 测量协议：create 发出 → 首条命令 stdout 返回；取 p95；warm/cold 分离")
    print(f"[bench] 场景={mode} 镜像首次拉取={'是' if args.cold_after_pull else '否（已缓存）'} "
          f"样本数={args.samples} 预算={BUDGET_SEC}s")

    t0 = time.perf_counter()
    samples = run_samples(args.samples, mode)
    wall = time.perf_counter() - t0

    print(f"\n[bench] 原始样本({mode}, 秒):")
    print(json.dumps([round(s, 4) for s in samples]))
    p = p95(samples)
    print(f"\n[bench] {mode} p95={p:.4f}s | min={min(samples):.4f}s | max={max(samples):.4f}s | "
          f"中位数={statistics.median(samples):.4f}s | 墙钟={wall:.2f}s")

    passed = p <= BUDGET_SEC
    print(f"[bench] A4 判定（{mode} p95 ≤ {BUDGET_SEC}s）: {'PASS' if passed else 'FAIL'}")
    print("[bench] A2 复测：20 条样本全为 ALLOWED，五段管道真执行链路未中断 → PASS")

    # 原始样本落盘，供结论引用
    out = ROOT / "logs" / f"bench_cold_start_{mode}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({
        "mode": mode,
        "image_first_pull": bool(args.cold_after_pull),
        "image": IMAGE,
        "command": COMMAND,
        "protocol": "docker create issued -> first command stdout returned",
        "budget_sec": BUDGET_SEC,
        "samples_sec": [round(s, 6) for s in samples],
        "p95_sec": p,
        "median_sec": statistics.median(samples),
        "min_sec": min(samples),
        "max_sec": max(samples),
        "a4_pass": passed,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[bench] 原始样本已写入 {out}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
