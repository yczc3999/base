"""Gemini 3.6 Flash Search/URL wire contract tests（WP-02 Checkpoint B；golden fixture + fake transport）。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.services.model_gateway.contracts import ModelRequest, ProviderError
from app.services.model_gateway.drivers.gemini import GeminiDriver

WIRE_DIR = Path(__file__).resolve().parents[3] / "tests" / "trading" / "fixtures" / "ai_wire" / "gemini"


def _load(name: str) -> str:
    return (WIRE_DIR / name).read_text()


def _transport(name: str, status: int = 200):
    async def transport(endpoint, *, headers, json, timeout):
        return status, _load(name)
    return transport


def _request() -> ModelRequest:
    return ModelRequest(
        role="verifier", stage="g5a", episode_id=1, attempt_no=1, experiment_variant="champion",
        requested_provider="gemini", requested_route="direct", requested_model="gemini-3.6-flash",
        network_policy="SEARCH_URL", allowed_tools=["search_url"],
        prompt_text="p", input_manifest={"k": "v"}, input_manifest_hash="a" * 64, sampling={},
    )


def test_success_search_url():
    driver = GeminiDriver(_transport("success_search.json"))
    response = asyncio.run(driver.request(_request()))
    assert "verified" in response.raw_text
    assert len(response.tool_receipts) == 1
    assert response.tool_receipts[0].source_urls == ["https://example.com/source"]


def test_5xx_error():
    driver = GeminiDriver(_transport("5xx.json", status=500))
    with pytest.raises(ProviderError, match="gemini_5xx"):
        asyncio.run(driver.request(_request()))
