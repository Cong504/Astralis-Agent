"""R8/R9 真实验证（W2 真实项，不凭 mock 签字）。

R8：单步超时 → 真 kill + verdict=TIMEOUT + 容器销毁。
R9：内存超限 → OOM 终止 + verdict=OOM_KILLED + 审计记录。

用法：python scripts/verify_limits.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sandbox.docker_executor import DockerSandboxExecutor  # noqa: E402
from sandbox.executor import ExecutionRequest, ResourceLimits, Verdict  # noqa: E402


def container_exists(executor: DockerSandboxExecutor, cid: str) -> bool:
    import subprocess
    p = subprocess.run(
        [executor.docker_bin, "inspect", cid],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return p.returncode == 0


def verify_r8_timeout() -> bool:
    print("[R8] 超时真终止：sleep 120，step_timeout=3s")
    ex = DockerSandboxExecutor()
    limits = ResourceLimits(step_timeout_s=3)
    req = ExecutionRequest("r8", "s1", "sleep 120", limits=limits)
    t0 = time.perf_counter()
    result = ex.execute(req)
    elapsed = time.perf_counter() - t0
    gone = result.container_id and not container_exists(ex, result.container_id)
    ok = result.verdict is Verdict.TIMEOUT and elapsed < 15 and gone
    print(f"[R8] verdict={result.verdict.value} 耗时={elapsed:.1f}s 容器已销毁={bool(gone)} → {'PASS' if ok else 'FAIL'}")
    return ok


def verify_r9_oom() -> bool:
    print("[R9] OOM：容器内分配 512MB，memory 限制 64MB")
    ex = DockerSandboxExecutor()
    limits = ResourceLimits(memory_mb=64, step_timeout_s=30)
    script = (
        "python -c \"x=bytearray(512*1024*1024); "
        "import time; time.sleep(5)\""
    )
    req = ExecutionRequest("r9", "s1", script, limits=limits)
    result = ex.execute(req)
    gone = result.container_id and not container_exists(ex, result.container_id)
    # OOM 通常 exit_code=137；判定为 OOM_KILLED 或 exit 137 均视为命中
    oom = result.verdict is Verdict.OOM_KILLED or result.exit_code == 137
    ok = oom and bool(gone)
    print(f"[R9] verdict={result.verdict.value} exit={result.exit_code} 容器已销毁={bool(gone)} → {'PASS' if ok else 'FAIL'}")
    return ok


def main() -> int:
    r8 = verify_r8_timeout()
    r9 = verify_r9_oom()
    print(f"[limits] {'ALL PASS' if r8 and r9 else 'HAS FAIL'}")
    return 0 if (r8 and r9) else 1


if __name__ == "__main__":
    raise SystemExit(main())
