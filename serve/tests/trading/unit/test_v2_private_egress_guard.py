"""WP-05 Checkpoint C：private egress guard（任何未注入 transport 的 connect/socket 立即失败；
fake transport calls > 0；真实网络调用 = 0）。"""

from __future__ import annotations

import asyncio

import pytest

from app.services.polymarket.base import PrivateSubmitPolicy
from app.services.polymarket.clob_trading_driver import (
    ACK,
    ClobTradingDriver,
    EgressTripwireError,
)
from app.services.polymarket.user_ws_driver import UserWsDriver


class _FakeAckClient:
    def __init__(self):
        self.post_order_calls = 0

    def post_order(self, signed_order):
        self.post_order_calls += 1
        from app.schemas.polymarket.clob_private import OrderResponse

        return OrderResponse(order_id="ord-1", status="live", success=True)


class _FakeSigned:
    pass


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
    outcome = await driver.submit_order(_FakeSigned())
    assert outcome.cls == ACK
    assert driver.transport_calls == 1
    assert client.post_order_calls == 1


@pytest.mark.asyncio
async def test_user_ws_driver_without_transport_immediately_fails():
    driver = UserWsDriver("wss://real.polymarket.invalid/ws/user")
    with pytest.raises(Exception) as exc:
        await driver.connect(auth_token=None)
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
    await driver.connect(auth_token=None)
    assert driver.transport_calls == 1
    assert ws.sent  # subscribe frame sent
    await driver.aclose()


def test_constructing_drivers_does_not_connect():
    """构造 Driver 本身绝不连接；只有 wire 方法调用才可能触碰 transport。"""
    ClobTradingDriver(client=None)
    UserWsDriver("wss://fake/user")
