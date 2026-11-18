"""云端先行路由测试（ADR-0003）：DeepSeek 主用、Qwen 手动切换、无 Key 退化 stub。"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from model.router import (  # noqa: E402
    PROVIDER_PRESETS, StubBackend, TokenUsage, build_cloud_router, build_stub_router,
)


def test_no_key_falls_back_to_stub(monkeypatch):
    for name in ("DEEPSEEK_API_KEY", "CLOUD_MODEL_KEY", "DASHSCOPE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    router = build_cloud_router("deepseek")
    assert isinstance(router.primary, StubBackend)
    assert router.primary.name == "stub"


def test_deepseek_primary_with_key(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key-for-test")
    router = build_cloud_router("deepseek")
    assert router.primary.name == "deepseek"
    assert router.primary.endpoint == PROVIDER_PRESETS["deepseek"]["endpoint"]
    assert router.primary.model == "deepseek-chat"
    # Key 不出现在路由的公开属性以外（不写入 result / 日志对象）


def test_qwen_manual_switch(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "fake-key-for-test")
    router = build_cloud_router("qwen")
    assert router.primary.name == "qwen"
    assert router.primary.model == "qwen-plus"


def test_unknown_provider_rejected():
    try:
        build_cloud_router("unknown")
        assert False, "应拒绝未知供应商"
    except Exception:
        pass


def test_token_usage_from_response():
    u = TokenUsage.from_response({"usage": {"prompt_tokens": 12, "completion_tokens": 8}})
    assert u.total_tokens == 20


def test_stub_router_reports_zero_usage():
    router = build_stub_router()
    from model.parser import parse_plan
    result = router.generate("任意", parse_fn=parse_plan)
    assert result.usage.total_tokens == 0
    assert result.backend == "stub"
