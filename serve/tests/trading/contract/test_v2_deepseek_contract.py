"""DeepSeek wire contract tests（WP-02 Checkpoint B；golden fixture + fake transport）。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.services.model_gateway.contracts import ModelRequest, ProviderError
from app.services.model_gateway.drivers.deepseek import DeepSeekDriver

WIRE_DIR = Path(__file__).resolve().parents[3] / "tests" / "trading" / "fixtures" / "ai_wire" / "deepseek"


def _load(name: str) -> str:
    return (WIRE_DIR / name).read_text()


def _transport(name: str, status: int = 200):
    async def transport(endpoint, *, headers, json, timeout):
        return status, _load(name)
    return transport


def _request() -> ModelRequest:
    return ModelRequest(
        role="planner_prior", stage="g4", episode_id=1, attempt_no=1, experiment_variant="champion",
        requested_provider="deepseek", requested_route="direct", requested_model="deepseek-v4-pro",
        network_policy="NONE", prompt_text="p", input_manifest={"k": "v"},
        input_manifest_hash="a" * 64, sampling={},
    )


def test_success_wire():
    driver = DeepSeekDriver(_transport("success.json"))
    response = asyncio.run(driver.request(_request()))
    assert response.returned_provider == "deepseek"
    assert response.returned_model == "deepseek-v4-pro"
    assert "prior" in response.raw_text
    assert response.input_tokens == 120


def test_429_error():
    driver = DeepSeekDriver(_transport("429.json", status=429))
    with pytest.raises(ProviderError, match="deepseek_rate_limited"):
        asyncio.run(driver.request(_request()))


def test_5xx_error():
    driver = DeepSeekDriver(_transport("500.json", status=500))
    with pytest.raises(ProviderError, match="deepseek_5xx"):
        asyncio.run(driver.request(_request()))


def test_truncated_response_rejected():
    driver = DeepSeekDriver(_transport("truncated.json"))
    with pytest.raises(ProviderError, match="deepseek_response_malformed"):
        asyncio.run(driver.request(_request()))


def test_network_none_forbids_tools():
    driver = DeepSeekDriver(_transport("success.json"))
    req = _request()
    with pytest.raises(ProviderError, match="network_none_with_tools"):
        driver._assert_no_network_for_none(ModelRequest(**{**req.__dict__, "allowed_tools": ["web"]}))
