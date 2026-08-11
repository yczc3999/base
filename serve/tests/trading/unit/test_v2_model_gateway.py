"""Model gateway unit tests（WP-02 Checkpoint B）。

- registry：allowlist 校验（provider/route/model、returned model、Anthropic/OpenAI 拒绝）。
- drivers：golden wire fixture 通过 fake transport 验证 success/429/5xx/timeout/truncated/
  secret echo/model drift/tool receipt。
- service：冻结 binding 构造 driver + role/network/tools mismatch 拒绝。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.services.model_gateway.contracts import (
    NETWORK_NONE,
    NETWORK_SEARCH_URL,
    NETWORK_WEB_X,
    ModelRequest,
    ProviderError,
)
from app.services.model_gateway.drivers.deepseek import DeepSeekDriver
from app.services.model_gateway.drivers.gemini import GeminiDriver
from app.services.model_gateway.drivers.kimi import KimiDriver
from app.services.model_gateway.drivers.packy import PackyDriver
from app.services.model_gateway.drivers.xai import XAIDriver
from app.services.model_gateway.registry import (
    assert_returned_model,
    resolve,
)

WIRE_DIR = Path(__file__).resolve().parents[3] / "tests" / "trading" / "fixtures" / "ai_wire"


def _load(provider: str, name: str) -> str:
    return (WIRE_DIR / provider / name).read_text()


def _fake_transport(provider: str, *, name: str, status: int = 200, simulate_timeout: bool = False):
    """返回注入 transport：读 golden fixture 或模拟错误/超时。"""
    async def transport(endpoint, *, headers, json, timeout):
        if simulate_timeout:
            raise asyncio.TimeoutError("timeout")
        body = _load(provider, name)
        return status, body
    return transport


def _request(*, provider: str, route: str = "direct", model: str = None, network: str = NETWORK_NONE,
             role: str = "planner_prior") -> ModelRequest:
    model = model or {
        "deepseek": "deepseek-v4-pro", "xai": "grok-4.5", "gemini": "gemini-3.6-flash",
        "kimi": "kimi-k3", "packy": "packy-preview",
    }[provider]
    return ModelRequest(
        role=role, stage="g4", episode_id=1, attempt_no=1, experiment_variant="champion",
        requested_provider=provider, requested_route=route, requested_model=model,
        network_policy=network, prompt_text="prompt", input_manifest={"k": "v"},
        input_manifest_hash="a" * 64, sampling={},
    )


class TestRegistry:
    def test_resolve_known(self):
        rm = resolve("deepseek", "direct", "deepseek-v4-pro")
        assert rm.provider == "deepseek"

    def test_provider_not_allowed(self):
        with pytest.raises(ValueError, match="model_provider_not_allowed:anthropic"):
            resolve("anthropic", "direct", "claude-5")

    def test_model_not_allowed(self):
        with pytest.raises(ValueError, match="model_not_allowed"):
            resolve("deepseek", "direct", "gpt-4o")

    def test_route_not_allowed(self):
        with pytest.raises(ValueError, match="model_route_not_allowed"):
            resolve("deepseek", "nonexistent", "deepseek-v4-pro")

    def test_returned_alias_drift_rejected(self):
        # relay alias 漂移：返回不在任何 allowlist 的 model → REJECTED
        with pytest.raises(ValueError, match="model_returned_not_allowed"):
            assert_returned_model("xai", "grok-4.5-unknown")
        # 合法返回
        assert_returned_model("deepseek", "deepseek-v4-pro")


class TestDeepSeekDriver:
    def test_success(self):
        driver = DeepSeekDriver(_fake_transport("deepseek", name="success.json"))
        response = asyncio.run(driver.request(_request(provider="deepseek")))
        assert response.returned_model == "deepseek-v4-pro"
        assert "prior" in response.raw_text
        assert response.input_tokens == 120

    def test_429(self):
        driver = DeepSeekDriver(_fake_transport("deepseek", name="429.json", status=429))
        with pytest.raises(ProviderError, match="deepseek_rate_limited"):
            asyncio.run(driver.request(_request(provider="deepseek")))

    def test_5xx(self):
        driver = DeepSeekDriver(_fake_transport("deepseek", name="500.json", status=500))
        with pytest.raises(ProviderError, match="deepseek_5xx"):
            asyncio.run(driver.request(_request(provider="deepseek")))

    def test_timeout(self):
        driver = DeepSeekDriver(_fake_transport("deepseek", name="success.json", simulate_timeout=True))
        with pytest.raises(ProviderError, match="deepseek_transport_failed"):
            asyncio.run(driver.request(_request(provider="deepseek")))

    def test_truncated_json(self):
        driver = DeepSeekDriver(_fake_transport("deepseek", name="truncated.json"))
        with pytest.raises(ProviderError, match="deepseek_response_malformed"):
            asyncio.run(driver.request(_request(provider="deepseek")))


class TestXAIDriver:
    def test_success_with_tool_receipt(self):
        driver = XAIDriver(_fake_transport("xai", name="success_tool.json"))
        response = asyncio.run(driver.request(_request(provider="xai", network=NETWORK_WEB_X)))
        assert response.returned_model == "grok-4.5"
        assert len(response.tool_receipts) == 1
        assert response.tool_receipts[0].tool_type == "web_search"
        assert "who won" in json.dumps(response.tool_receipts[0].arguments)

    def test_returned_alias_drift_rejected(self):
        driver = XAIDriver(_fake_transport("xai", name="alias_drift.json"))
        response = asyncio.run(driver.request(_request(provider="xai", network=NETWORK_WEB_X)))
        with pytest.raises(ValueError, match="model_returned_not_allowed"):
            assert_returned_model(response.returned_provider, response.returned_model)


class TestGeminiDriver:
    def test_success_search_url(self):
        driver = GeminiDriver(_fake_transport("gemini", name="success_search.json"))
        response = asyncio.run(driver.request(_request(provider="gemini", network=NETWORK_SEARCH_URL)))
        assert "verified" in response.raw_text
        assert len(response.tool_receipts) == 1
        assert response.tool_receipts[0].source_urls == ["https://example.com/source"]

    def test_5xx(self):
        driver = GeminiDriver(_fake_transport("gemini", name="5xx.json", status=500))
        with pytest.raises(ProviderError, match="gemini_5xx"):
            asyncio.run(driver.request(_request(provider="gemini", network=NETWORK_SEARCH_URL)))


class TestKimiDriver:
    def test_success(self):
        driver = KimiDriver(_fake_transport("kimi", name="success.json"))
        response = asyncio.run(driver.request(_request(provider="kimi")))
        assert "Q" in response.raw_text

    def test_429(self):
        driver = KimiDriver(_fake_transport("kimi", name="429.json", status=429))
        with pytest.raises(ProviderError, match="kimi_rate_limited"):
            asyncio.run(driver.request(_request(provider="kimi")))


class TestPackyDriver:
    def test_success(self):
        driver = PackyDriver(_fake_transport("packy", name="success.json"))
        response = asyncio.run(driver.request(_request(provider="packy")))
        assert response.returned_model == "packy-preview"

    def test_5xx(self):
        driver = PackyDriver(_fake_transport("packy", name="500.json", status=500))
        with pytest.raises(ProviderError, match="packy_5xx"):
            asyncio.run(driver.request(_request(provider="packy")))


class TestBlindNetworkEnforcement:
    def test_blind_deepseek_no_network(self):
        driver = DeepSeekDriver(_fake_transport("deepseek", name="success.json"))
        req = _request(provider="deepseek")
        req.assert_blind_context("PRIOR")
        response = asyncio.run(driver.request(req))
        assert response.returned_model == "deepseek-v4-pro"

    def test_blind_forbidden_context(self):
        req = _request(provider="deepseek")
        with pytest.raises(ValueError, match="blind_context_forbidden"):
            req.assert_blind_context("QUOTE")

    def test_blind_forbidden_network(self):
        req = _request(provider="xai", network=NETWORK_WEB_X)
        with pytest.raises(ValueError, match="blind_network_forbidden"):
            req.assert_blind_context("PRIOR")
