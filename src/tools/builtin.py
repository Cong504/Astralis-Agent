"""5 个基础工具实现（W2.6，DEVELOPMENT_PLAN.md 2.6）。

边界（ARCHITECTURE.md §2 / SANDBOX_RULES.md §1.1）：
  - 工具不直接执行副作用；需要执行脚本/命令的工具必须把命令交给 SandboxExecutor。
  - 只暴露受控能力：路径须在 ToolSpec 声明范围内；高危由执行层规则拦截。
  - 本文件不含真实容器操作——执行由注入的 executor 完成，便于 mock 验证。

真实项说明：工具「在容器里真能执行」必须靠真实镜像验收（见 W2-plan.md 真实镜像验收清单），
mock 测试只验证参数构造、路径校验与执行器派发逻辑，不作为真执行签字。
"""

from __future__ import annotations

from typing import Any, Protocol

from sandbox.executor import ExecutionRequest, ExecutionResult, MountPolicy, Verdict
from tools.mcp_bridge import ToolCall, ToolResult, validate_call


class ExecutorLike(Protocol):
    def execute(self, request: ExecutionRequest) -> ExecutionResult: ...


class BuiltinTools:
    """5 个基础工具；所有命令类工具经 executor.execute() 派发到沙箱。"""

    def __init__(self, executor: ExecutorLike, task_id: str = "t", mounts: MountPolicy | None = None):
        self.executor = executor
        self.task_id = task_id
        self.mounts = mounts or MountPolicy()
        self._step = 0

    def _next_step(self) -> str:
        self._step += 1
        return f"step-{self._step}"

    def _dispatch(self, call: ToolCall, command: str) -> ToolResult:
        rejection = validate_call(call)
        if rejection:
            return ToolResult(tool=call.tool, ok=False, verdict="REJECTED", error=rejection)
        request = ExecutionRequest(
            task_id=call.task_id,
            step_id=call.step_id or self._next_step(),
            command=command,
            mounts=self.mounts,
            user_confirmed=call.confirmed,
        )
        result = self.executor.execute(request)
        ok = result.verdict is Verdict.ALLOWED
        return ToolResult(
            tool=call.tool,
            ok=ok,
            # command 字段供 §7 审计（原始命令完整留痕，不截断）
            output={"stdout": result.stdout, "exit_code": result.exit_code, "command": command},
            verdict=result.verdict.value,
            matched_rule=result.matched_rule,
            error=result.stderr if not ok else "",
        )

    def dir_scan(self, call: ToolCall) -> ToolResult:
        path = _safe_path(call.args.get("path", "/workspace"))
        if path is None:
            return ToolResult(tool="dir_scan", ok=False, error="非法路径（须在 /workspace 下）")
        return self._dispatch(call, f"find {path} -maxdepth 2 -type f | sort")

    def file_read(self, call: ToolCall) -> ToolResult:
        path = _safe_path(call.args.get("path", ""))
        if path is None:
            return ToolResult(tool="file_read", ok=False, error="非法路径（须在 /workspace 下）")
        return self._dispatch(call, f"cat {path}")

    def script_run(self, call: ToolCall) -> ToolResult:
        script = str(call.args.get("script", ""))
        if not script.strip():
            return ToolResult(tool="script_run", ok=False, error="script 不能为空")
        return self._dispatch(call, script)

    def report_write(self, call: ToolCall) -> ToolResult:
        target = str(call.args.get("target", "/tmp/agent-scratch/report.md"))
        # 允许 /tmp/agent-scratch（临时）与 /output（用户确认后的宿主输出挂载，S2 报告落库）
        allowed = target.startswith("/tmp/agent-scratch/") or (
            target.startswith("/output/") and bool(self.mounts.output_src)
        )
        if not allowed:
            return ToolResult(tool="report_write", ok=False, error="仅允许写入 /tmp/agent-scratch 或已确认的 /output")
        content = str(call.args.get("content", ""))
        # 用 base64 传递内容，避免命令注入；容器内有标准库 base64 可用
        import base64
        encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
        # chmod 666：Windows Docker Desktop 挂载卷上，容器 uid 10001 建的文件宿主默认不可读；
        # 报告非敏感（数据流输出），放开读权限以便宿主落库读取（S2 冒烟实测发现）。
        cmd = f"printf '%s' {encoded} | base64 -d > {target} && chmod 666 {target}"
        return self._dispatch(call, cmd)

    def log_record(self, call: ToolCall) -> ToolResult:
        # log_record 不进沙箱（needs_sandbox=False）：仅回显结构化事件，由 Runtime 写库。
        event = call.args.get("event", "")
        return ToolResult(tool="log_record", ok=True, output={
            "task_id": call.task_id, "step_id": call.step_id, "event": event,
        })

    def call(self, call: ToolCall) -> ToolResult:
        handlers = {
            "dir_scan": self.dir_scan,
            "file_read": self.file_read,
            "script_run": self.script_run,
            "report_write": self.report_write,
            "log_record": self.log_record,
        }
        handler = handlers.get(call.tool)
        if handler is None:
            return ToolResult(tool=call.tool, ok=False, error=f"未知工具: {call.tool}")
        return handler(call)


def _safe_path(path: str) -> str | None:
    """路径校验：须为 /workspace 下的绝对路径，拒绝 .. 穿越（SANDBOX_RULES.md §5）。"""
    if not isinstance(path, str) or not path.startswith("/workspace"):
        return None
    if ".." in path.split("/"):
        return None
    return path
