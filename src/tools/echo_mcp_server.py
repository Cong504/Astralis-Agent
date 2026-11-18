"""A3 验证用的最小 MCP echo 子进程：stdio 逐行 JSON-RPC，回显工具调用参数。

不是完整 MCP 实现，只验证「运行时 ↔ 工具进程」握手往返可行。
"""

from __future__ import annotations

import json
import sys

TOOLS = ["dir_scan", "file_read", "script_run", "report_write", "log_record"]


def handle(message: dict) -> dict:
    method = message.get("method", "")
    mid = message.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": "0.1", "server": "echo"}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": [{"name": t} for t in TOOLS]}}
    if method == "tools/call":
        params = message.get("params", {})
        return {
            "jsonrpc": "2.0", "id": mid,
            "result": {"ok": True, "output": {"echo": params}, "verdict": "ALLOWED"},
        }
    if method == "shutdown":
        return {"jsonrpc": "2.0", "id": mid, "result": {"ok": True}}
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "unknown method"}}


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
            response = handle(message)
        except json.JSONDecodeError:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
