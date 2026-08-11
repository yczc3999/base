"""WP-05 Checkpoint C：private egress guard（任何未注入 transport 的 connect/socket 立即失败；
fake transport calls > 0；真实网络调用 = 0）。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.schemas.polymarket.clob_private import PrivateApiCredentials
from app.services.polymarket.base import PrivateSubmitPolicy
from app.services.polymarket.clob_trading_driver import (
    ACK,
    ClobTradingDriver,
    EgressTripwireError,
)
from app.schemas.polymarket.common import PolymarketError
from app.services.polymarket.user_ws_driver import UserWsDriver
from runtimes.trading.execution import PrivateExecutionRuntime


def _credentials() -> PrivateApiCredentials:
    return PrivateApiCredentials(
        api_key="fixture-api-key",
        secret="fixture-secret",
        passphrase="fixture-passphrase",
    )


class _FakeAckClient:
    def __init__(self):
        self.post_order_calls = 0
        self.credentials = SimpleNamespace(key="fixture-owner")
        self.signer = "0x" + "11" * 20

    def post_order(self, signed_order):
        self.post_order_calls += 1
        from app.schemas.polymarket.clob_private import OrderResponse

        return OrderResponse(order_id="ord-1", status="live", success=True)


class _FakeSigned:
    builder = "0x" + "0" * 64
    expiration = 0
    maker = "0x" + "22" * 20
    maker_amount = 10
    metadata = "0x" + "0" * 64
    order_type = "GTC"
    post_only = False
    salt = 1
    side = "BUY"
    signature = "0x" + "ab" * 65
    signature_type = 3
    signer = maker
    taker_amount = 5
    timestamp = 2
    token_id = "123"


@pytest.mark.asyncio
async def test_clob_driver_without_client_immediately_fails():
    driver = ClobTradingDriver(client=None, policy=PrivateSubmitPolicy())
    with pytest.raises(EgressTripwireError):
        await driver.submit_order(_FakeSigned())
    with pytest.raises(EgressTripwireError):
        await driver.cancel_orders(["o1"])
    with pytest.raises(EgressTripwireError):
        await driver.list_open_orders()
    with pytest.raises(EgressTripwireError):
        await driver.list_trades()
    with pytest.raises(EgressTripwireError):
        await driver.send_heartbeat("")
    assert driver.transport_calls == 0


@pytest.mark.asyncio
async def test_clob_driver_fake_transport_calls_gt_zero():
    client = _FakeAckClient()
    driver = ClobTradingDriver(client=client, policy=PrivateSubmitPolicy())
    outcome = await driver.submit_order(driver.prepare_signed_order(_FakeSigned()))
    assert outcome.cls == ACK
    assert driver.transport_calls == 1
    assert client.post_order_calls == 1


@pytest.mark.asyncio
async def test_clob_driver_forbids_wallet_deployment_even_with_client():
    class _WalletClient:
        def setup_gasless_wallet(self):  # pragma: no cover - must never run
            raise AssertionError("wallet deployment reached")

    driver = ClobTradingDriver(_WalletClient())
    with pytest.raises(PolymarketError, match="wire_wallet_deployment_forbidden"):
        await driver.setup_gasless_wallet()


@pytest.mark.asyncio
async def test_user_ws_driver_without_transport_immediately_fails():
    driver = UserWsDriver("wss://real.polymarket.invalid/ws/user")
    with pytest.raises(Exception) as exc:
        await driver.connect(_credentials())
    assert "tripwire" in str(exc.value)


@pytest.mark.asyncio
async def test_user_ws_driver_fake_transport_calls_gt_zero():
    class _FakeWs:
        def __init__(self):
            self.sent = []

        async def send(self, text):
            self.sent.append(text)

        async def recv(self):
            await asyncio.sleep(30)
            raise RuntimeError("timeout")

        async def close(self):
            pass

    ws = _FakeWs()

    class _Connector:
        def __await__(self):
            async def _get():
                return ws
            return _get().__await__()

    driver = UserWsDriver("wss://fake/user", ws_connect=lambda: _Connector())
    await driver.connect(_credentials())
    assert driver.transport_calls == 1
    assert ws.sent  # subscribe frame sent
    await driver.aclose()


def test_constructing_drivers_does_not_connect():
    """构造 Driver 本身绝不连接；只有 wire 方法调用才可能触碰 transport。"""
    ClobTradingDriver(client=None)
    UserWsDriver("wss://fake/user")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exchange_address", "driver_exchange", "reason"),
    [
        (None, None, "submit_frozen_exchange_identity_missing"),
        (
            "0x" + "1" * 40,
            "0x" + "2" * 40,
            "submit_driver_exchange_identity_mismatch",
        ),
    ],
)
async def test_submit_identity_failure_never_calls_signer(
    exchange_address, driver_exchange, reason,
):
    class _SessionsMustNotRun:
        def __call__(self):  # pragma: no cover - identity gate precedes DB/signing
            raise AssertionError("database preflight reached")

    class _SignerProbe:
        chain_id = 137

        def __init__(self):
            self.exchange_address = driver_exchange
            self.sign_calls = 0

        async def create_signed_order(self, **_kwargs):
            self.sign_calls += 1
            raise AssertionError("signer reached")

    driver = _SignerProbe()
    runtime = PrivateExecutionRuntime(
        _SessionsMustNotRun(), exchange_address=exchange_address,
    )
    with pytest.raises(RuntimeError, match=reason):
        await runtime.submit_order(
            submit_input=SimpleNamespace(
                token_id="token", price="0.5", size="1", side="SELL", post_only=False,
            ),
            owner="owner",
            driver=driver,
        )
    assert driver.sign_calls == 0
