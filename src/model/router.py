"""云端优先模型路由（1.8，ADR-0003）。

路线：DeepSeek 主用，Qwen 手动切换备选（不做自动互备）；无 Key 时退化为 StubBackend。
云端 Key 只在 Runtime 侧从环境变量读取，绝不进容器/日志/包（SANDBOX_RULES.md §6）。
Model 层只负责调用 API 并上报 token usage，不累计预算（预算归 Runtime 层）。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# 通用 Key 兜底名；各供应商优先读自己的环境变量
CLOUD_KEY_ENV = "CLOUD_MODEL_KEY"
MODEL_PROVIDER_ENV = "MODEL_PROVIDER"
_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.json"

# 供应商预设（手动切换：设置 MODEL_PROVIDER 或改 config.json 的 model.provider）
PROVIDER_PRESETS: dict[str, dict[str, str]] = {
    "deepseek": {
        "endpoint": "https://api.deepseek.com/v1/chat/completions",
        "model": "deepseek-chat",
        "key_env": "DEEPSEEK_API_KEY",
    },
    "qwen": {
        "endpoint": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "model": "qwen-plus",
        "key_env": "DASHSCOPE_API_KEY",
    },
}


class BackendError(RuntimeError):
    pass


@dataclass(frozen=True)
class TokenUsage:
    """单次模型调用的 token 用量（来自 API response.usage）。"""
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @classmethod
    def from_response(cls, body: dict[str, Any]) -> "TokenUsage":
        usage = body.get("usage") or {}
        return cls(
            prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
            completion_tokens=int(usage.get("completion_tokens", 0) or 0),
        )


class Backend:
    name: str = "base"

    def __init__(self) -> None:
        self.last_usage: TokenUsage = TokenUsage()

    def generate(self, prompt: str) -> str:
        raise NotImplementedError


class StubBackend(Backend):
    """无 Key 开发桩：返回合法计划，usage 为 0。日志须标注 backend=stub。"""

    name = "stub"

    def __init__(self, plan: dict[str, Any] | None = None):
        super().__init__()
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
        self.last_usage = TokenUsage()
        return json.dumps(self._plan, ensure_ascii=False)


class OpenAICompatibleBackend(Backend):
    """OpenAI 兼容云端 endpoint（DeepSeek / Qwen 共用），提取 usage。"""

    def __init__(self, name: str, endpoint: str, model: str, api_key: str | None = None,
                 timeout: float = 30.0):
        super().__init__()
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
            self.last_usage = TokenUsage.from_response(body)
            return body["choices"][0]["message"]["content"]
        except (urllib.error.URLError, KeyError, IndexError, json.JSONDecodeError, TimeoutError) as exc:
            raise BackendError(f"{self.name} 调用失败: {type(exc).__name__}") from exc


@dataclass
class RouteResult:
    text: str
    backend: str
    fallback: bool
    attempts: list[str]
    usage: TokenUsage = field(default_factory=TokenUsage)
    normalization: dict[str, Any] = field(default_factory=dict)


class ModelRouter:
    """单后端路由（云端先行，不做自动互备）。

    对解析失败或后端错误重试 max_retry 次；Qwen 由用户手动切换，不在失败时自动接管。
    """

    def __init__(self, primary: Backend, fallback: Backend | None = None):
        self.primary = primary
        self.fallback = fallback  # 仅兼容旧接口；云端先行下不自动使用

    def generate(self, prompt: str, parse_fn: Callable[[str], Any], max_retry: int = 1) -> RouteResult:
        attempts: list[str] = []
        backend = self.primary
        last_exc: Exception | None = None
        for attempt in range(max_retry + 1):
            try:
                text = backend.generate(prompt)
                parse_fn(text)
                return RouteResult(
                    text=text, backend=backend.name, fallback=False,
                    attempts=attempts + [f"{backend.name}#{attempt}"], usage=backend.last_usage,
                )
            except Exception as exc:  # noqa: BLE001 - 后端或解析错误都重试
                last_exc = exc
                attempts.append(f"{backend.name}#{attempt}: {type(exc).__name__}")
        raise BackendError(f"{backend.name} 调用/解析失败（已重试 {max_retry} 次）: {attempts}") from last_exc


def _load_model_provider() -> str:
    provider = os.getenv(MODEL_PROVIDER_ENV, "").strip().lower()
    if provider:
        return provider
    try:
        with _CONFIG_PATH.open(encoding="utf-8") as fh:
            return json.load(fh).get("model", {}).get("provider", "deepseek")
    except (OSError, json.JSONDecodeError):
        return "deepseek"


def build_stub_router() -> ModelRouter:
    """无 Key 开发路由：stub 单后端，日志标注 backend=stub。"""
    return ModelRouter(primary=StubBackend())


def build_cloud_router(provider: str | None = None) -> ModelRouter:
    """云端路由：按 provider 选 DeepSeek / Qwen（手动切换），无 Key 退化 stub。

    Key 读取顺序：供应商专属环境变量 → 通用 CLOUD_MODEL_KEY。
    不打印 Key 值，不把 Key 写入任何返回对象。
    """
    provider = (provider or _load_model_provider()).strip().lower()
    preset = PROVIDER_PRESETS.get(provider)
    if preset is None:
        raise BackendError(f"未知模型供应商: {provider}（可选: {', '.join(PROVIDER_PRESETS)}）")
    key = os.getenv(preset["key_env"]) or os.getenv(CLOUD_KEY_ENV)
    if not key:
        return build_stub_router()
    return ModelRouter(primary=OpenAICompatibleBackend(
        name=provider,
        endpoint=os.getenv(f"{provider.upper()}_ENDPOINT", preset["endpoint"]),
        model=os.getenv(f"{provider.upper()}_MODEL", preset["model"]),
        api_key=key,
    ))


def build_cloud_fallback_router(local_endpoint: str | None = None) -> ModelRouter:
    """兼容旧调用点：语义已改为云端先行（ADR-0003），参数忽略。"""
    return build_cloud_router()
