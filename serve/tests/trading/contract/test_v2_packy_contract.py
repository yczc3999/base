"""Packy OpenAI-compatible 无搜索 relay wire contract tests（WP-02 Checkpoint B；golden fixture + fake transport）。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.services.model_gateway.contracts import ModelRequest, ProviderError
from app.services.model_gateway.drivers.packy import PackyDriver

WIRE_DIR = Path(__file__).resolve().parents[3] / "tests" / "trading" / "fixtures" / "ai_wire" / "packy"


def _load(name: str) -> str:
    return (WIRE_DIR / name).read_text()


def _transport(name: str, status: int = 200):
    async def transport(endpoint, *, headers, json, timeout):
        return status, _load(name)
    return transport


def _request() -> ModelRequest:
    return ModelRequest(
        role="joint_forecaster", stage="g6", episode_id=1, attempt_no=1, experiment_variant="champion",
        requested_provider="packy", requested_route="direct", requested_model="packy-preview",
        network_policy="NONE", prompt_text="p", input_manifest={"k": "v"},
        input_manifest_hash="a" * 64, sampling={},
    )


def test_success_wire():
    driver = PackyDriver(_transport("success.json"))
    response = asyncio.run(driver.request(_request()))
    assert response.returned_model == "packy-preview"
    assert "fallback" in response.raw_text


def test_5xx_error():
    driver = PackyDriver(_transport("500.json", status=500))
    with pytest.raises(ProviderError, match="packy_5xx"):
        asyncio.run(driver.request(_request()))
