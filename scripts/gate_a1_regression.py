"""G1 A1' 回归门禁（W3 3.8，CI 强制）。

硬规则（W3-plan.md §1）：改 system prompt 或扩工具集 → 必重跑 A1' 合法性回归。
  - 命中触发路径且无云端 Key：输出 SKIP 与明确原因，退出 0，但**不静默通过**
    （CI 日志与门禁记录都留痕）；有 Key：真跑 10 次并按判据卡合并。
  - 未命中触发路径：SKIP（no relevant change），退出 0。
  - **回归必须走完整确认交互链路**：A1' 判据中「合规」含「能被用户确认」；
    本门禁调用 bench_a1_legality.py（其 prompt 含完整确认标记要求），不桩掉确认步骤。

用法：
  python scripts/gate_a1_regression.py <changed_file> [...]   # CI 传变更文件列表
  python scripts/gate_a1_regression.py                        # 无参数：读 git diff HEAD~1 HEAD
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from compat.encoding import force_utf8_stdio  # noqa: E402

force_utf8_stdio()

# G1 触发路径（相对仓库根）
TRIGGER_PATHS = (
    "src/runtime/task_runner.py",   # PLANNING_PROMPT (system prompt)
    "src/schema/plan.schema.json",  # tool 枚举
    "src/model/parser.py",          # TOOL_ALIASES / normalize_plan
    "src/tools/builtin.py",         # 工具集实现
)

# 供应商 Key 环境变量名（与 router.py 一致，不打印值）
KEY_ENVS = ("DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY", "CLOUD_MODEL_KEY")


def changed_files_from_git() -> list[str]:
    proc = subprocess.run(
        ["git", "diff", "--name-only", "HEAD~1", "HEAD"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def has_key() -> bool:
    return any(os.getenv(name) for name in KEY_ENVS)


def record(result: dict) -> None:
    out = ROOT / "logs" / "a1_gate.jsonl"
    out.parent.mkdir(exist_ok=True)
    with out.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(result, ensure_ascii=False) + "\n")


def main(argv: list[str]) -> int:
    files = argv if argv else changed_files_from_git()
    triggered = [p for p in files if any(p.replace("\\", "/").endswith(t) for t in TRIGGER_PATHS)]
    key_present = has_key()
    ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds")

    if not triggered:
        result = {"ts": ts, "decision": "SKIP", "reason": "no relevant change",
                  "changed": files, "key_present": key_present}
        print(f"[G1] SKIP：未命中触发路径（变更 {len(files)} 个文件）。门禁记录已留痕。")
        record(result)
        return 0

    print(f"[G1] 命中触发路径：{triggered}")
    print(f"[G1] 云端 Key {'存在' if key_present else '缺失'}（只检查存在性，不打印值）")

    if not key_present:
        # 显式 SKIP，不静默通过：CI 日志与门禁记录都留痕，附原因与待办
        result = {"ts": ts, "decision": "SKIP", "reason": "no cloud key (explicit, not silent)",
                  "triggered": triggered, "key_present": False,
                  "todo": "有 Key 的环境必须重跑 python scripts/bench_a1_legality.py"}
        print("[G1] SKIP（显式，非静默）：无云端 Key，无法跑真实 A1' 回归。")
        print("[G1] 待办：有 Key 的环境必须补跑 `python scripts/bench_a1_legality.py`（raw_valid≥8/10）。")
        record(result)
        return 0

    # 有 Key：真跑 A1' 回归，透传退出码；确认交互链路由 bench 脚本的 prompt 契约保证
    print("[G1] 执行真实 A1' 回归（含完整确认交互链路契约）...")
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "bench_a1_legality.py")], cwd=ROOT)
    result = {"ts": ts, "decision": "PASS" if proc.returncode == 0 else "FAIL",
              "reason": "real A1' regression run", "triggered": triggered,
              "key_present": True, "exit_code": proc.returncode}
    record(result)
    print(f"[G1] 回归退出码 {proc.returncode} → {'PASS' if proc.returncode == 0 else 'FAIL（卡合并）'}")
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
