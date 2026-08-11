"""WP-05 Checkpoint C：ClobTradingDriver 单测（fake transport、单次发送、UNKNOWN 盲重发=0、
egress tripwire）。"""

from __future__ import annotations

import time
from decimal import Decimal
from types import SimpleNamespace

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
    PreparedSignedOrder,
    canonical_order_body_hash,
)


class _SdkCancelResponse:
    def __init__(self, canceled, not_canceled):
        self.canceled = canceled
        self.not_canceled = not_canceled


class _FakeSignedOrder:
    def __init__(self, salt=1, timestamp=2, order_id="ord-1"):
        self.maker = "0x" + "a" * 40
        self.signer = "0x" + "a" * 40
        self.builder = "0x" + "0" * 64
        self.metadata = "0x" + "0" * 64
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
        self.credentials = SimpleNamespace(key="fixture-owner")
        self.signer = "0x" + "1" * 40
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

    def get_order(self, *, order_id):
        return SimpleNamespace(
            id=order_id,
            order_id=order_id,
            token_id="tok-1",
            side="BUY",
            price=Decimal("0.5"),
            size=Decimal("10"),
            original_size=Decimal("10"),
            size_matched=Decimal("0"),
            status="live",
            created_at=None,
        )


class _FakeRejectClient:
    def __init__(self):
        self.credentials = SimpleNamespace(key="fixture-owner")
        self.signer = "0x" + "1" * 40
        self.post_order_calls = 0

    def post_order(self, signed_order):
        self.post_order_calls += 1
        return OrderResponse(order_id=None, status="unmatched", success=False, error_msg="bad order")


class _FakeIndeterminateClient:
    def __init__(self):
        self.credentials = SimpleNamespace(key="fixture-owner")
        self.signer = "0x" + "1" * 40
        self.post_order_calls = 0

    def post_order(self, signed_order):
        self.post_order_calls += 1
        return OrderResponse(order_id=None, status=None, success=False, error_msg=None)


class _FakeRaisingClient:
    def __init__(self, exc):
        self.credentials = SimpleNamespace(key="fixture-owner")
        self.signer = "0x" + "1" * 40
        self.post_order_calls = 0
        self._exc = exc

    def post_order(self, signed_order):
        self.post_order_calls += 1
        raise self._exc


def _driver(client, clock=None):
    return ClobTradingDriver(
        client,
        policy=PrivateSubmitPolicy(),
        clock=clock or time.monotonic,
        trusted_time_provider=time.time,
    )


def _prepared(driver):
    return driver.prepare_signed_order(_FakeSignedOrder())


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
    outcome = await driver.submit_order(_prepared(driver))
    assert outcome.cls == ACK
    assert outcome.order_id == "ord-ack"
    assert client.post_order_calls == 1
    assert driver.transport_calls == 1


@pytest.mark.asyncio
async def test_submit_rejected_single_send():
    client = _FakeRejectClient()
    driver = _driver(client)
    outcome = await driver.submit_order(_prepared(driver))
    assert outcome.cls == REJECTED
    assert client.post_order_calls == 1


@pytest.mark.asyncio
async def test_submit_indeterminate_200_no_blind_resend():
    client = _FakeIndeterminateClient()
    driver = _driver(client)
    outcome = await driver.submit_order(_prepared(driver))
    assert outcome.cls == UNKNOWN
    # 盲重发=0：post_order 只被调用一次。
    assert client.post_order_calls == 1


@pytest.mark.asyncio
async def test_submit_timeout_unknown_no_blind_resend():
    client = _FakeRaisingClient(PolymarketError("wire_read_timeout"))
    driver = _driver(client)
    outcome = await driver.submit_order(_prepared(driver))
    assert outcome.cls == UNKNOWN
    assert client.post_order_calls == 1


@pytest.mark.asyncio
async def test_submit_5xx_unknown_no_blind_resend():
    client = _FakeRaisingClient(PolymarketError("wire_http_5xx", http_status=500))
    driver = _driver(client)
    outcome = await driver.submit_order(_prepared(driver))
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
async def test_single_order_lookup_only_maps_explicit_404_to_absence():
    client = _FakeAckClient()
    driver = _driver(client)
    order = await driver.get_order(order_id="o1")
    assert order is not None and order.order_id == "o1"

    from polymarket import RequestRejectedError

    class Missing(_FakeAckClient):
        def get_order(self, *, order_id):
            raise RequestRejectedError("not found", status=404)

    assert await _driver(Missing()).get_order(order_id="o1") is None

    class Failed(_FakeAckClient):
        def get_order(self, *, order_id):
            raise RequestRejectedError("unavailable", status=503)

    with pytest.raises(PolymarketError, match="order_indeterminate"):
        await _driver(Failed()).get_order(order_id="o1")


def test_trade_normalization_uses_the_accounts_maker_order_and_preserves_fee_economics():
    client = _FakeAckClient()
    driver = _driver(client)
    maker = SimpleNamespace(
        order_id="maker-order",
        owner="fixture-owner",
        side="SELL",
        price=Decimal("0.60"),
        matched_amount=Decimal("7"),
    )
    item = SimpleNamespace(
        id="trade-maker",
        token_id="tok-1",
        trader_side="MAKER",
        taker_order_id="someone-else",
        side="BUY",
        price=Decimal("0.40"),
        size=Decimal("7"),
        fee_rate_bps=Decimal("400"),
        maker_orders=(maker,),
        matched_at=None,
    )
    normalized = driver._normalize_trade(item)
    assert normalized.order_id == "maker-order"
    assert normalized.side == "SELL"
    assert normalized.price == Decimal("0.60")
    assert normalized.size == Decimal("7")
    assert normalized.fee == 0

    item.trader_side = "TAKER"
    item.taker_order_id = "taker-order"
    normalized = driver._normalize_trade(item)
    assert normalized.order_id == "taker-order"
    assert normalized.side == "BUY"
    assert normalized.fee == Decimal("0.0672")


@pytest.mark.asyncio
async def test_create_signed_order_delegates():
    client = _FakeAckClient()
    driver = _driver(client)
    signed = await driver.create_signed_order(
        token_id="tok-1", price="0.5", size="10", side="BUY",
    )
    assert client.create_limit_calls == 1
    assert isinstance(signed, PreparedSignedOrder)
    assert signed.salt == 1
    assert canonical_order_body_hash(signed) == signed.body_hash
    assert "fixture-owner" not in repr(signed)
    assert _FakeSignedOrder().signature not in repr(signed)


@pytest.mark.asyncio
async def test_create_signed_order_requires_fresh_trusted_time_before_signer():
    client = _FakeAckClient()
    missing = ClobTradingDriver(client, unix_clock=lambda: 1_000.0)
    with pytest.raises(PolymarketError, match="wire_trusted_time_required"):
        await missing.create_signed_order(
            token_id="tok-1", price="0.5", size="10", side="BUY",
        )
    assert client.create_limit_calls == 0

    stale = ClobTradingDriver(
        client,
        unix_clock=lambda: 1_001.0,
        trusted_time_provider=lambda: 1_000.0,
    )
    with pytest.raises(PolymarketError, match="wire_clock_skew_exceeded"):
        await stale.create_signed_order(
            token_id="tok-1", price="0.5", size="10", side="BUY",
        )
    assert client.create_limit_calls == 0


@pytest.mark.asyncio
async def test_submit_rejects_raw_sdk_order_convenience_bypass():
    driver = _driver(_FakeAckClient())
    with pytest.raises(PolymarketError, match="wire_prepared_order_required"):
        await driver.submit_order(_FakeSignedOrder())


def test_clock_skew_check_stops_submit():
    driver = _driver(client=_FakeAckClient())
    driver.assert_clock_skew(unix_now=1_000_000_000.0, trusted_server_time=1_000_000_000.5)
    with pytest.raises(PolymarketError, match="wire_clock_skew_exceeded"):
        driver.assert_clock_skew(unix_now=1_000_000_000.0, trusted_server_time=1_000_000_000.501)


def test_l2_hmac_input_canonical():
    driver = _driver(client=_FakeAckClient())
    msg = driver.l2_hmac_input(
        unix_seconds=1700000000, method="post", path_without_query="/order", body=b"{}",
    )
    assert msg == "1700000000POST/order{}"


def test_execution_identity_is_explicit_and_checked_before_signing_boundary():
    exchange = "0x1111111111111111111111111111111111111111"
    driver = ClobTradingDriver(
        _FakeAckClient(), chain_id=137, exchange_address=exchange,
    )
    assert driver.chain_id == 137
    assert driver.exchange_address_for("tok-1") == exchange
    driver.assert_execution_identity(
        token_id="tok-1", chain_id=137, exchange_address=exchange.upper(),
    )
    with pytest.raises(PolymarketError, match="wire_chain_identity_mismatch"):
        driver.assert_execution_identity(
            token_id="tok-1", chain_id=80002, exchange_address=exchange,
        )
    with pytest.raises(PolymarketError, match="wire_exchange_identity_mismatch"):
        driver.assert_execution_identity(
            token_id="tok-1",
            chain_id=137,
            exchange_address="0x2222222222222222222222222222222222222222",
        )

    unbound = ClobTradingDriver(_FakeAckClient())
    with pytest.raises(PolymarketError, match="wire_chain_identity_mismatch"):
        unbound.assert_execution_identity(
            token_id="tok-1", chain_id=137, exchange_address=exchange,
        )
