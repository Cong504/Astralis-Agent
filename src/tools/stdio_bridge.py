"""MCP Stdio 桥接最小实现（A3 假设验证，W2.6）。

A3（W1-plan.md §3）：先用 Stdio echo 打通「运行时 ↔ 工具进程」一次调用握手。
成功信号：完成一次 initialize → tools/list → tools/call 进程间往返。
不依赖真沙箱（按用户裁决并行做、不抢主线资源）。

若 Stdio 集成阻塞 >1 天，按 W1-plan.md §6 回退自定义插件接口并出 ADR。
本实现是轻量 JSON-RPC over stdio 握手，非完整 MCP 协议栈。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from tools.mcp_bridge import BUILTIN_TOOLS, MCPBridge, ToolCall, ToolResult, ToolSpec


class StdioMCPBridge(MCPBridge):
    """通过子进程 stdio 交换 JSON-RPC 消息的最小桥接。"""

    def __init__(self, server_script: Path | str | None = None):
        self.server_script = Path(server_script) if server_script else (
            Path(__file__).resolve().parent / "echo_mcp_server.py"
        )
        self._proc: subprocess.Popen | None = None

    def _send(self, message: dict[str, Any]) -> dict[str, Any]:
        if self._proc is None or self._proc.stdin is None or self._proc.stdout is None:
            raise RuntimeError("MCP 子进程未启动")
        self._proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self._proc.stdin.flush()
        line = self._proc.stdout.readline()
        if not line:
            raise RuntimeError("MCP 子进程无响应（可能已退出）")
        return json.loads(line)

    def start(self) -> dict[str, Any]:
        """启动子进程并完成 initialize 握手。"""
        self._proc = subprocess.Popen(
            [sys.executable, str(self.server_script)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        return self._send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})

    def list_tools(self) -> list[ToolSpec]:
        self._send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        return list(BUILTIN_TOOLS)

    def call(self, invocation: ToolCall) -> ToolResult:
        resp = self._send({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": invocation.tool, "arguments": invocation.args},
        })
        result = resp.get("result", {})
        return ToolResult(
            tool=invocation.tool,
            ok=bool(result.get("ok", True)),
            output=result.get("output", {}),
            verdict=result.get("verdict", "ALLOWED"),
        )

    def shutdown(self) -> None:
        if self._proc is not None:
            try:
                self._send({"jsonrpc": "2.0", "id": 99, "method": "shutdown", "params": {}})
            except Exception:
                pass
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None
