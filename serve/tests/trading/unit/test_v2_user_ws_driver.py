"""WP-05 Checkpoint C：UserWsDriver 单测（PING/PONG、断线 RECONCILING、egress tripwire）。"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.schemas.polymarket.clob_private import PrivateApiCredentials
from app.schemas.polymarket.common import PolymarketError
from app.schemas.polymarket.user_ws import UserOrderEvent, parse_user_ws_frame
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


def _credentials() -> PrivateApiCredentials:
    return PrivateApiCredentials(
        api_key="fixture-api-key",
        secret="fixture-secret",
        passphrase="fixture-passphrase",
    )


@pytest.mark.asyncio
async def test_connect_subscribes_with_exact_nested_auth_and_safe_receipt():
    connector, ws = _frame_ws([b'{"event_type":"pong"}'])
    driver = UserWsDriver("wss://fake/user", policy=_policy(), ws_connect=connector, clock=asyncio.get_event_loop().time)
    credentials = _credentials()
    await driver.connect(credentials)
    assert json.loads(ws.sent[0]) == {
        "auth": {
            "apiKey": "fixture-api-key",
            "secret": "fixture-secret",
            "passphrase": "fixture-passphrase",
        },
        "type": "user",
    }
    persisted_views = repr(driver.receipts) + repr(credentials)
    assert "fixture-api-key" not in persisted_views
    assert "fixture-secret" not in persisted_views
    assert "fixture-passphrase" not in persisted_views
    await driver.aclose()


@pytest.mark.asyncio
async def test_next_frame_order_event_and_artifact_hash():
    connector, ws = _frame_ws([b'{"event_type":"order","order_id":"o1","token_id":"tok-1","side":"BUY","price":"0.5","size":"10"}'])
    driver = UserWsDriver("wss://fake/user", policy=_policy(), ws_connect=connector, clock=asyncio.get_event_loop().time)
    await driver.connect(_credentials())
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
    await driver.connect(_credentials())
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
    await driver.connect(_credentials())
    with pytest.raises(PolymarketError, match="wire_ws_disconnect"):
        await driver.next_frame()
    # 断线 → RECONCILING（调用方据此执行 REST 回补）。
    assert driver.terminal_reason is not None
    await driver.aclose()


@pytest.mark.asyncio
async def test_egress_tripwire_without_transport():
    driver = UserWsDriver("wss://fake/user", policy=_policy(), clock=asyncio.get_event_loop().time)
    with pytest.raises(PolymarketError, match="wire_egress_tripwire"):
        await driver.connect(_credentials())


def test_frame_unknown_preserved():
    frame = parse_user_ws_frame('{"event_type":"weird"}')
    assert frame.event_type == "unknown"


@pytest.mark.asyncio
async def test_private_frame_is_not_retained_and_sensitive_extras_are_removed():
    raw_frame = json.dumps(
        {
            "event_type": "order",
            "order_id": "o1",
            "token_id": "tok-1",
            "side": "BUY",
            "price": "0.5",
            "size": "10",
            "signature": "private-frame-signature",
            "apiKey": "private-frame-api-key",
            "provider_note": "retained",
            "future": {
                "secret": "nested-private-secret",
                "safe_value": "retained-nested",
            },
        },
        separators=(",", ":"),
    )
    connector, _ws = _frame_ws([raw_frame])
    driver = UserWsDriver(
        "wss://fake/user",
        policy=_policy(),
        ws_connect=connector,
        clock=asyncio.get_event_loop().time,
    )
    await driver.connect(_credentials())
    message = await driver.next_frame()

    assert not hasattr(message, "raw_text")
    assert not hasattr(message.frame, "raw_text")
    assert message.frame.raw_extra == {
        "provider_note": "retained",
        "future": {"safe_value": "retained-nested"},
    }
    persisted_views = (
        repr(message)
        + repr(message.frame)
        + json.dumps(message.frame.model_dump(mode="json"), sort_keys=True)
        + repr(message.receipts)
    )
    for private_value in (
        raw_frame,
        "private-frame-signature",
        "private-frame-api-key",
        "nested-private-secret",
    ):
        assert private_value not in persisted_views
    await driver.aclose()


def test_malformed_private_frame_only_retains_controlled_error():
    frame = parse_user_ws_frame("private-raw-frame-secret")
    assert frame.event_type == "unknown"
    assert frame.parse_error == "malformed_json"
    assert "private-raw-frame-secret" not in repr(frame)
    assert "private-raw-frame-secret" not in json.dumps(frame.model_dump(mode="json"))
