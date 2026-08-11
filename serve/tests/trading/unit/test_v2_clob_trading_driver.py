"""WP-05 Checkpoint C：ClobTradingDriver 单测（fake transport、单次发送、UNKNOWN 盲重发=0、
egress tripwire）。"""

from __future__ import annotations

import time
from decimal import Decimal

import pytest

from app.schemas.polymarket.clob_private import OrderResponse
from app.schemas.polymarket.common import PolymarketError
from app.services.polymarket.base import PrivateSubmitPolicy
from app.services.polymarket.clob_trading_driver import (
    ACK,
    REJECTED,
    UNKNOWN,
    ClobTradingDriver,
    EgressTripwireError,
)


class _SdkCancelResponse:
    def __init__(self, canceled, not_canceled):
        self.canceled = canceled
        self.not_canceled = not_canceled


class _FakeSignedOrder:
    def __init__(self, salt=1, timestamp=2, order_id="ord-1"):
        self.maker = "0x" + "a" * 40
        self.signer = "0x" + "a" * 40
        self.signature_type = 3
        self.signature = "0x" + "b" * 130
        self.token_id = "tok-1"
        self.maker_amount = 10
        self.taker_amount = 1
        self.side = "BUY"
        self.salt = salt
        self.timestamp = timestamp
        self.expiration = 0
        self.order_type = "GTC"
        self.post_only = False
        self.order_id = order_id


class _FakeAckClient:
    def __init__(self):
        self.post_order_calls = 0
        self.cancel_calls = 0
        self.create_limit_calls = 0

    def post_order(self, signed_order):
        self.post_order_calls += 1
        return OrderResponse(order_id="ord-ack", status="live", success=True)

    def cancel_orders(self, *, order_ids):
        self.cancel_calls += 1
        return _SdkCancelResponse(canceled=tuple(order_ids), not_canceled={})

    def create_limit_order(self, **kwargs):
        self.create_limit_calls += 1
        return _FakeSignedOrder()


class _FakeRejectClient:
    def __init__(self):
        self.post_order_calls = 0

    def post_order(self, signed_order):
        self.post_order_calls += 1
        return OrderResponse(order_id=None, status="unmatched", success=False, error_msg="bad order")


class _FakeIndeterminateClient:
    def __init__(self):
        self.post_order_calls = 0

    def post_order(self, signed_order):
        self.post_order_calls += 1
        return OrderResponse(order_id=None, status=None, success=False, error_msg=None)


class _FakeRaisingClient:
    def __init__(self, exc):
        self.post_order_calls = 0
        self._exc = exc

    def post_order(self, signed_order):
        self.post_order_calls += 1
        raise self._exc


def _driver(client, clock=None):
    return ClobTradingDriver(client, policy=PrivateSubmitPolicy(), clock=clock or time.monotonic)


@pytest.mark.asyncio
async def test_egress_tripwire_no_client():
    driver = _driver(client=None)
    assert driver.has_client is False
    with pytest.raises(EgressTripwireError):
        await driver.submit_order(_FakeSignedOrder())
    with pytest.raises(EgressTripwireError):
        await driver.cancel_orders(["o1"])
    with pytest.raises(EgressTripwireError):
        await driver.list_open_orders()
    with pytest.raises(EgressTripwireError):
        await driver.send_heartbeat("")


@pytest.mark.asyncio
async def test_submit_single_send_ack():
    client = _FakeAckClient()
    driver = _driver(client)
    outcome = await driver.submit_order(_FakeSignedOrder())
    assert outcome.cls == ACK
    assert outcome.order_id == "ord-ack"
    assert client.post_order_calls == 1
    assert driver.transport_calls == 1


@pytest.mark.asyncio
async def test_submit_rejected_single_send():
    client = _FakeRejectClient()
    driver = _driver(client)
    outcome = await driver.submit_order(_FakeSignedOrder())
    assert outcome.cls == REJECTED
    assert client.post_order_calls == 1


@pytest.mark.asyncio
async def test_submit_indeterminate_200_no_blind_resend():
    client = _FakeIndeterminateClient()
    driver = _driver(client)
    outcome = await driver.submit_order(_FakeSignedOrder())
    assert outcome.cls == UNKNOWN
    # 盲重发=0：post_order 只被调用一次。
    assert client.post_order_calls == 1


@pytest.mark.asyncio
async def test_submit_timeout_unknown_no_blind_resend():
    client = _FakeRaisingClient(PolymarketError("wire_read_timeout"))
    driver = _driver(client)
    outcome = await driver.submit_order(_FakeSignedOrder())
    assert outcome.cls == UNKNOWN
    assert client.post_order_calls == 1


@pytest.mark.asyncio
async def test_submit_5xx_unknown_no_blind_resend():
    client = _FakeRaisingClient(PolymarketError("wire_http_5xx", http_status=500))
    driver = _driver(client)
    outcome = await driver.submit_order(_FakeSignedOrder())
    assert outcome.cls == UNKNOWN
    assert client.post_order_calls == 1


@pytest.mark.asyncio
async def test_cancel_orders_normalized():
    client = _FakeAckClient()
    driver = _driver(client)
    result = await driver.cancel_orders(["o1", "o2"])
    assert result.success is True
    assert {item.order_id for item in result.items} == {"o1", "o2"}


@pytest.mark.asyncio
async def test_create_signed_order_delegates():
    client = _FakeAckClient()
    driver = _driver(client)
    signed = await driver.create_signed_order(
        token_id="tok-1", price="0.5", size="10", side="BUY",
    )
    assert client.create_limit_calls == 1
    assert signed.salt == 1


def test_clock_skew_check_stops_submit():
    driver = _driver(client=_FakeAckClient())
    driver.assert_clock_skew(unix_now=1_000_000_000, trusted_server_time=1_000_000_010)
    with pytest.raises(PolymarketError, match="wire_clock_skew_exceeded"):
        driver.assert_clock_skew(unix_now=1_000_000_000, trusted_server_time=1_000_001_000)


def test_l2_hmac_input_canonical():
    driver = _driver(client=_FakeAckClient())
    msg = driver.l2_hmac_input(
        unix_seconds=1700000000, method="post", path_without_query="/order", body=b"{}",
    )
    assert msg == "1700000000POST/order{}"
