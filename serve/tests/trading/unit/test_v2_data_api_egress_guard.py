"""WP-05 private Data API egress tripwire and public-driver compatibility."""

from __future__ import annotations

import httpx
import pytest

from app.schemas.polymarket.common import PolymarketError
from app.services.polymarket.base import (
    HttpPolymarketDriver,
    REASON_EGRESS_TRIPWIRE,
    WirePolicy,
    explicit_http_proxy,
)
from app.services.polymarket.data_api_driver import DataApiDriver
from app.services.polymarket.service import PolymarketService


@pytest.mark.asyncio
@pytest.mark.parametrize("wire_method", ["trades", "positions"])
async def test_data_api_without_transport_trips_before_httpx_client(
    monkeypatch: pytest.MonkeyPatch,
    wire_method: str,
) -> None:
    constructed = 0

    def _forbidden_client(*args, **kwargs):
        nonlocal constructed
        constructed += 1
        raise AssertionError("httpx_client_must_not_be_constructed")

    monkeypatch.setattr("app.services.polymarket.base.httpx.AsyncClient", _forbidden_client)
    driver = DataApiDriver()

    with pytest.raises(PolymarketError) as caught:
        await getattr(driver, wire_method)(
            headers={
                "poly-api-key": "private-api-key",
                "poly-signature": "private-signature",
            }
        )

    assert caught.value.reason_code == REASON_EGRESS_TRIPWIRE
    assert constructed == 0
    assert len(caught.value.receipts) == 1
    receipt = caught.value.receipts[0]
    assert receipt.error_code == REASON_EGRESS_TRIPWIRE
    assert receipt.http_status is None
    assert receipt.redacted_header_names == ("poly-api-key", "poly-signature")
    assert "private-api-key" not in repr(receipt)
    assert "private-signature" not in repr(receipt)


@pytest.mark.asyncio
async def test_service_data_api_default_is_guarded() -> None:
    driver = PolymarketService().data_api()
    with pytest.raises(PolymarketError, match=REASON_EGRESS_TRIPWIRE):
        await driver.positions()


@pytest.mark.asyncio
async def test_data_api_with_injected_transport_uses_only_that_transport() -> None:
    calls: list[httpx.Request] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={"data": [], "next_cursor": None},
            request=request,
        )

    driver = DataApiDriver(transport=httpx.MockTransport(_handler))
    result = await driver.positions(limit=25)

    assert len(calls) == 1
    assert calls[0].method == "GET"
    assert calls[0].url.host == "data-api.polymarket.com"
    assert calls[0].url.path == "/positions"
    assert calls[0].url.params["limit"] == "25"
    assert result.typed.items == []


@pytest.mark.asyncio
async def test_public_http_driver_keeps_default_network_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructed = 0
    client_kwargs: dict = {}

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            nonlocal constructed
            constructed += 1
            client_kwargs.update(kwargs)

        async def request(self, method, url, **kwargs) -> httpx.Response:
            request = httpx.Request(method, url, params=kwargs.get("params"))
            return httpx.Response(200, json={"ok": True}, request=request)

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr("app.services.polymarket.base.httpx.AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(
        "app.services.polymarket.base.explicit_http_proxy",
        lambda environ=None: "http://127.0.0.1:10808",
    )
    driver = HttpPolymarketDriver(
        "https://public-fixture.invalid",
        policy=WirePolicy(max_retries=0),
    )

    result = await driver.get_json("/status")

    assert constructed == 1
    assert client_kwargs.get("trust_env") is False
    assert client_kwargs.get("proxy") == "http://127.0.0.1:10808"
    assert "transport" not in client_kwargs
    assert result.typed == {"ok": True}


def test_explicit_http_proxy_skips_socks_and_prefers_https() -> None:
    assert (
        explicit_http_proxy(
            {
                "ALL_PROXY": "socks5h://127.0.0.1:10809",
                "HTTPS_PROXY": "http://127.0.0.1:10808",
                "HTTP_PROXY": "http://127.0.0.1:9",
            }
        )
        == "http://127.0.0.1:10808"
    )
    assert (
        explicit_http_proxy(
            {
                "ALL_PROXY": "socks5h://127.0.0.1:10809",
                "HTTPS_PROXY": "socks5h://127.0.0.1:10809",
            }
        )
        is None
    )
    assert explicit_http_proxy({}) is None
