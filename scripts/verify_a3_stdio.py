"""A3 假设验证：MCP Stdio 进程间工具调用握手（W1-plan.md §3）。

成功信号：initialize → tools/list → tools/call 一次往返完成。
不依赖真沙箱（并行项）。全绿则 A3 通过；若 Stdio 集成阻塞则回退自定义插件接口出 ADR。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tools.mcp_bridge import ToolCall  # noqa: E402
from tools.stdio_bridge import StdioMCPBridge  # noqa: E402


def main() -> int:
    print("[A3] 启动 Stdio MCP 子进程并握手...")
    bridge = StdioMCPBridge()
    try:
        t0 = time.perf_counter()
        init = bridge.start()
        tools = bridge.list_tools()
        result = bridge.call(ToolCall("a3", "s1", "dir_scan", {"path": "/workspace"}))
        elapsed = time.perf_counter() - t0

        ok = (
            init.get("result", {}).get("protocolVersion") == "0.1"
            and len(tools) == 5
            and result.ok
            and result.verdict == "ALLOWED"
            and result.output.get("echo", {}).get("name") == "dir_scan"
        )
        print(f"[A3] initialize: {init.get('result')}")
        print(f"[A3] tools/list: {len(tools)} 个工具")
        print(f"[A3] tools/call 回显: {result.output}")
        print(f"[A3] 往返耗时: {elapsed:.3f}s")
        print(f"[A3] {'PASS' if ok else 'FAIL'}（进程间握手完成）")
        return 0 if ok else 1
    finally:
        bridge.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
