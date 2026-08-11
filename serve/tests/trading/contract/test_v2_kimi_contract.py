"""Kimi K3 joint forecaster wire contract tests（WP-02 Checkpoint B；golden fixture + fake transport）。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.services.model_gateway.contracts import ModelRequest, ProviderError
from app.services.model_gateway.drivers.kimi import KimiDriver

WIRE_DIR = Path(__file__).resolve().parents[3] / "tests" / "trading" / "fixtures" / "ai_wire" / "kimi"


def _load(name: str) -> str:
    return (WIRE_DIR / name).read_text()


def _transport(name: str, status: int = 200):
    async def transport(endpoint, *, headers, json, timeout):
        return status, _load(name)
    return transport


def _request() -> ModelRequest:
    return ModelRequest(
        role="joint_forecaster", stage="g6", episode_id=1, attempt_no=1, experiment_variant="champion",
        requested_provider="kimi", requested_route="direct", requested_model="kimi-k3",
        network_policy="NONE", prompt_text="p", input_manifest={"k": "v"},
        input_manifest_hash="a" * 64, sampling={},
    )


def test_success_wire():
    driver = KimiDriver(_transport("success.json"))
    response = asyncio.run(driver.request(_request()))
    assert "Q" in response.raw_text
    assert response.returned_model == "kimi-k3"


def test_429_error():
    driver = KimiDriver(_transport("429.json", status=429))
    with pytest.raises(ProviderError, match="kimi_rate_limited"):
        asyncio.run(driver.request(_request()))
