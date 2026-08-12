"""WP-07C：生产 transport factory 单元测试（不触真实 provider）。"""

from __future__ import annotations

import asyncio
import os

import pytest

from app.services.model_gateway.contracts import ProviderError
from app.services.model_gateway.transport import build_transport_factory


def _run(coro):
    return asyncio.run(coro)


def test_missing_credential_fail_closed(monkeypatch):
    monkeypatch.delenv("PM_V2_DEEPSEEK_API_KEY", raising=False)
    factory = build_transport_factory()
    with pytest.raises(ProviderError, match="model_credential_missing"):
        factory("deepseek")


def test_disallowed_provider_rejected(monkeypatch):
    monkeypatch.setenv("PM_V2_DEEPSEEK_API_KEY", "sk-x")
    factory = build_transport_factory()
    for provider in ("gemini", "anthropic", "openai"):
        with pytest.raises(ProviderError):
            factory(provider)


def test_base_url_override_applies(monkeypatch):
    monkeypatch.setenv("PM_V2_PACKY_API_KEY", "sk-packy")
    captured = {}

    class _Resp:
        status_code = 200
        text = "{}"

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["headers"] = headers
            return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    factory = build_transport_factory(
        base_url_overrides={"packy": "https://relay.example/v1"}
    )
    transport = factory("packy")
    status, body = _run(transport(endpoint="/v1/chat/completions", headers={}, json={}))
    assert captured["url"] == "https://relay.example/v1/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer sk-packy"


def test_authorization_not_overridable_by_driver_headers(monkeypatch):
    monkeypatch.setenv("PM_V2_XAI_API_KEY", "xai-real")
    captured = {}

    class _Resp:
        status_code = 200
        text = "{}"

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            captured["headers"] = headers
            return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    factory = build_transport_factory()
    transport = factory("xai")
    # driver 试图注入伪造 Authorization 必须被忽略
    _run(transport(endpoint="/v1/chat/completions",
                   headers={"Authorization": "Bearer fake"}, json={}))
    assert captured["headers"]["Authorization"] == "Bearer xai-real"
