"""本地/云端双后端路由（1.8）。

规则：本地优先，本地失败（超时/非法 JSON 重试耗尽/API 错误）自动切云端，不中断主流程。
云端 Key 只在 Runtime 侧从环境变量读取，绝不进容器、不进日志（SANDBOX_RULES.md §6）。
当前环境无 GPU、无 Key，提供 StubBackend 供 W1 骨架端到端演示。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

# 云端 Key 只从这些环境变量名读取（名称不打印值）
CLOUD_KEY_ENV = "CLOUD_MODEL_KEY"


class BackendError(RuntimeError):
    pass


class Backend:
    name: str = "base"

    def generate(self, prompt: str) -> str:
        raise NotImplementedError


class StubBackend(Backend):
    """W1 骨架桩：返回一个合法计划，用于无 Key/无 GPU 环境打通链路。"""

    name = "stub"

    def __init__(self, plan: dict[str, Any] | None = None):
        self._plan = plan or {
            "task_id": "stub-task",
            "goal": "统计指定目录下的文件数量与类型分布",
            "steps": [
                {
                    "step_id": "step-1",
                    "action": "扫描目录并统计文件类型分布",
                    "tool": "dir_scan",
                    "args": {"path": "/workspace/data"},
                    "risk_level": "low",
                },
                {
                    "step_id": "step-2",
                    "action": "生成统计报告",
                    "tool": "report_write",
                    "args": {"target": "/tmp/agent-scratch/report.md"},
                    "risk_level": "low",
                },
            ],
        }

    def generate(self, prompt: str) -> str:
        return json.dumps(self._plan, ensure_ascii=False)


class OpenAICompatibleBackend(Backend):
    """通用 OpenAI 兼容 endpoint（本地 vLLM 或云端 API 共用）。"""

    def __init__(self, name: str, endpoint: str, model: str, api_key: str | None = None,
                 timeout: float = 10.0):
        self.name = name
        self.endpoint = endpoint
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    def generate(self, prompt: str) -> str:
        payload = json.dumps({
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(self.endpoint, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            return body["choices"][0]["message"]["content"]
        except (urllib.error.URLError, KeyError, IndexError, json.JSONDecodeError, TimeoutError) as exc:
            raise BackendError(f"{self.name} 调用失败: {type(exc).__name__}") from exc


@dataclass
class RouteResult:
    text: str
    backend: str
    fallback: bool
    attempts: list[str]


class ModelRouter:
    """本地优先；主后端失败则切兜底后端。"""

    def __init__(self, primary: Backend, fallback: Backend):
        self.primary = primary
        self.fallback = fallback

    def generate(self, prompt: str, parse_fn: Callable[[str], Any]) -> RouteResult:
        attempts: list[str] = []
        try:
            text = self.primary.generate(prompt)
            parse_fn(text)  # 连带校验，非法 JSON 触发降级
            return RouteResult(text, self.primary.name, False, [self.primary.name])
        except Exception as exc:  # noqa: BLE001 - 主后端任何失败都降级
            attempts.append(f"{self.primary.name}: {type(exc).__name__}")
        text = self.fallback.generate(prompt)
        parse_fn(text)
        return RouteResult(text, self.fallback.name, True, attempts + [self.fallback.name])


def build_stub_router() -> ModelRouter:
    """当前环境：主用 stub，兜底也用 stub（不同实例），保证链路可跑。"""
    return ModelRouter(primary=StubBackend(), fallback=StubBackend())


def build_cloud_fallback_router(local_endpoint: str | None = None) -> ModelRouter:
    """生产配置：本地优先，云端兜底。

    从环境变量读 Key（只读存在性，不打印值）；无 Key 时 fallback 退化为 stub，
    保证链路不中断，但日志会标注 backend=stub，不会误判为真实云端。
    """
    local = OpenAICompatibleBackend(
        name="local",
        endpoint=local_endpoint or "http://localhost:8000/v1/chat/completions",
        model="qwen2.5-14b-instruct",
    )
    key = os.getenv(CLOUD_KEY_ENV)
    if key:
        fallback: Backend = OpenAICompatibleBackend(
            name="cloud",
            endpoint=os.getenv("CLOUD_MODEL_ENDPOINT", "https://api.example.com/v1/chat/completions"),
            model=os.getenv("CLOUD_MODEL_NAME", "qwen-plus"),
            api_key=key,
        )
    else:
        fallback = StubBackend()
    return ModelRouter(primary=local, fallback=fallback)
