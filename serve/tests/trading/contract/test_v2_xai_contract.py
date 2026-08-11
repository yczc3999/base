"""xAI Grok 4.5 Web/X wire contract tests（WP-02 Checkpoint B；golden fixture + fake transport）。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.services.model_gateway.contracts import ModelRequest, ProviderError
from app.services.model_gateway.drivers.xai import XAIDriver
from app.services.model_gateway.registry import assert_returned_model

WIRE_DIR = Path(__file__).resolve().parents[3] / "tests" / "trading" / "fixtures" / "ai_wire" / "xai"


def _load(name: str) -> str:
    return (WIRE_DIR / name).read_text()


def _transport(name: str, status: int = 200):
    async def transport(endpoint, *, headers, json, timeout):
        return status, _load(name)
    return transport


def _request() -> ModelRequest:
    return ModelRequest(
        role="researcher", stage="g5a", episode_id=1, attempt_no=1, experiment_variant="champion",
        requested_provider="xai", requested_route="direct", requested_model="grok-4.5",
        network_policy="WEB_X", allowed_tools=["web_search"],
        prompt_text="p", input_manifest={"k": "v"}, input_manifest_hash="a" * 64, sampling={},
    )


def test_success_with_tool_receipt():
    driver = XAIDriver(_transport("success_tool.json"))
    response = asyncio.run(driver.request(_request()))
    assert response.returned_model == "grok-4.5"
    assert len(response.tool_receipts) == 1
    receipt = response.tool_receipts[0]
    assert receipt.tool_type == "web_search"
    assert receipt.provider_tool_call_id == "call_1"
    assert "who won" in str(receipt.arguments)


def test_returned_alias_drift_rejected():
    driver = XAIDriver(_transport("alias_drift.json"))
    response = asyncio.run(driver.request(_request()))
    with pytest.raises(ValueError, match="model_returned_not_allowed"):
        assert_returned_model(response.returned_provider, response.returned_model)


def test_429_error():
    driver = XAIDriver(_transport("429.json", status=429))
    with pytest.raises(ProviderError, match="xai_rate_limited"):
        asyncio.run(driver.request(_request()))
