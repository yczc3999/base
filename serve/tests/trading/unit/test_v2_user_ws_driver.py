"""WP-05 Checkpoint C：UserWsDriver 单测（PING/PONG、断线 RECONCILING、egress tripwire）。"""

from __future__ import annotations

import asyncio

import pytest

from app.schemas.polymarket.common import PolymarketError
from app.schemas.polymarket.user_ws import UserOrderEvent
from app.services.polymarket.base import REASON_EGRESS_TRIPWIRE
from app.services.polymarket.user_ws_driver import UserWsDriver, UserWsPolicy


class _FakeWs:
    """极简 fake WS：send 收 PING 回 PONG；recv 从队列取帧。"""

    def __init__(self, frames, *, pong_on_ping=True):
        self.sent: list[str] = []
        self._frames = list(frames)
        self._pong_on_ping = pong_on_ping
        self._closed = False

    async def send(self, text):
        self.sent.append(text)
        if self._pong_on_ping and text == "PING":
            self._frames.append("PONG")

    async def recv(self):
        if not self._frames:
            await asyncio.sleep(60)
            raise RuntimeError("no frame")
        return self._frames.pop(0)

    async def close(self):
        self._closed = True


def _frame_ws(frames):
    """返回 ws_connect 工厂 + 共享 ws。"""
    ws = _FakeWs(frames)
    return (lambda: _FakeWsConnector(ws)), ws


class _FakeWsConnector:
    def __init__(self, ws):
        self._ws = ws

    def __await__(self):
        async def _get():
            return self._ws
        return _get().__await__()


def _policy():
    return UserWsPolicy(
        connect_timeout_s=1.0, recv_timeout_s=0.2, ping_interval_s=0.05, pong_timeout_s=0.3,
    )


@pytest.mark.asyncio
async def test_connect_subscribes_and_sends_redacted_auth():
    connector, ws = _frame_ws([b'{"event_type":"pong"}'])
    driver = UserWsDriver("wss://fake/user", policy=_policy(), ws_connect=connector, clock=asyncio.get_event_loop().time)
    await driver.connect(auth_token=None)
    subscribe = ws.sent[0]
    assert '"type":"user"' in subscribe
    assert "REDACTED" in subscribe
    await driver.aclose()


@pytest.mark.asyncio
async def test_next_frame_order_event_and_artifact_hash():
    connector, ws = _frame_ws([b'{"event_type":"order","order_id":"o1","token_id":"tok-1","side":"BUY","price":"0.5","size":"10"}'])
    driver = UserWsDriver("wss://fake/user", policy=_policy(), ws_connect=connector, clock=asyncio.get_event_loop().time)
    await driver.connect(auth_token=None)
    message = await driver.next_frame()
    assert isinstance(message.frame, UserOrderEvent)
    assert message.frame.order_id == "o1"
    assert len(message.artifact_hash) == 64
    assert driver.receive_seq == 1
    await driver.aclose()


@pytest.mark.asyncio
async def test_ping_pong_keeps_alive():
    connector, ws = _frame_ws([])
    driver = UserWsDriver("wss://fake/user", policy=_policy(), ws_connect=connector, clock=asyncio.get_event_loop().time)
    await driver.connect(auth_token=None)
    # 等待 ping 循环发出 PING 并收到 PONG。
    for _ in range(10):
        await asyncio.sleep(0.02)
        if "PING" in ws.sent:
            break
    assert "PING" in ws.sent
    assert driver.terminal_reason is None
    await driver.aclose()


@pytest.mark.asyncio
async def test_disconnect_sets_terminal_reason_reconciling():
    class _DisconnectWs(_FakeWs):
        async def recv(self):
            raise RuntimeError("socket closed")

    ws = _DisconnectWs([])
    connector = lambda: _FakeWsConnector(ws)  # noqa: E731
    driver = UserWsDriver("wss://fake/user", policy=_policy(), ws_connect=connector, clock=asyncio.get_event_loop().time)
    await driver.connect(auth_token=None)
    with pytest.raises(PolymarketError, match="wire_ws_disconnect"):
        await driver.next_frame()
    # 断线 → RECONCILING（调用方据此执行 REST 回补）。
    assert driver.terminal_reason is not None
    await driver.aclose()


@pytest.mark.asyncio
async def test_egress_tripwire_without_transport():
    driver = UserWsDriver("wss://fake/user", policy=_policy(), clock=asyncio.get_event_loop().time)
    with pytest.raises(PolymarketError, match="wire_egress_tripwire"):
        await driver.connect(auth_token=None)


def test_frame_unknown_preserved():
    from app.schemas.polymarket.user_ws import parse_user_ws_frame

    frame = parse_user_ws_frame('{"event_type":"weird"}')
    assert frame.event_type == "unknown"
