"""WP-05 Checkpoint C：User WS contract（reconcile watermark；断线 → RECONCILING）。"""

from __future__ import annotations

import asyncio

import pytest

from app.schemas.polymarket.clob_private import PrivateApiCredentials
from app.schemas.polymarket.common import PolymarketError
from app.services.polymarket.user_ws_driver import UserWsDriver, UserWsPolicy


class _FakeWs:
    def __init__(self, frames, *, drop_after=None):
        self.sent: list[str] = []
        self._frames = list(frames)
        self._drop_after = drop_after
        self._reads = 0

    async def send(self, text):
        self.sent.append(text)
        if text == "PING":
            self._frames.append("PONG")

    async def recv(self):
        self._reads += 1
        if self._drop_after is not None and self._reads > self._drop_after:
            raise RuntimeError("socket closed")
        if not self._frames:
            await asyncio.sleep(30)
            raise RuntimeError("no frame")
        return self._frames.pop(0)

    async def close(self):
        pass


class _Connector:
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
async def test_user_ws_watermark_increments_per_frame():
    ws = _FakeWs([
        b'{"event_type":"order","order_id":"o1","token_id":"tok-1","side":"BUY","price":"0.5","size":"1"}',
        b'{"event_type":"trade","trade_id":"t1","order_id":"o1","token_id":"tok-1","side":"BUY","price":"0.5","size":"1"}',
        b'{"event_type":"pong"}',
    ])
    driver = UserWsDriver(
        "wss://fake/user", policy=_policy(), ws_connect=lambda: _Connector(ws),
        clock=asyncio.get_event_loop().time,
    )
    await driver.connect(_credentials())
    m1 = await driver.next_frame()
    m2 = await driver.next_frame()
    assert driver.receive_seq == 2
    # 每帧递增 → 可作为 reconcile watermark。
    assert m2.receive_seq == m1.receive_seq + 1
    assert m1.artifact_hash != m2.artifact_hash
    await driver.aclose()


@pytest.mark.asyncio
async def test_user_ws_disconnect_sets_reconciling_watermark_boundary():
    ws = _FakeWs(
        [b'{"event_type":"order","order_id":"o1","token_id":"tok-1","side":"BUY","price":"0.5","size":"1"}'],
        drop_after=1,
    )
    driver = UserWsDriver(
        "wss://fake/user", policy=_policy(), ws_connect=lambda: _Connector(ws),
        clock=asyncio.get_event_loop().time,
    )
    await driver.connect(_credentials())
    m = await driver.next_frame()
    assert m.receive_seq == 1
    with pytest.raises(PolymarketError, match="wire_ws_disconnect"):
        await driver.next_frame()
    # 断线 → RECONCILING：watermark 停在最后已处理帧，REST 从该 watermark 回补。
    assert driver.terminal_reason is not None
    assert driver.receive_seq == 1
    await driver.aclose()


@pytest.mark.asyncio
async def test_ping_pong_liveness_no_pong_timeout():
    ws = _FakeWs([])
    driver = UserWsDriver(
        "wss://fake/user", policy=_policy(), ws_connect=lambda: _Connector(ws),
        clock=asyncio.get_event_loop().time,
    )
    await driver.connect(_credentials())
    for _ in range(10):
        await asyncio.sleep(0.02)
        if "PING" in ws.sent:
            break
    assert "PING" in ws.sent
    # PING → 收到文本 PONG（作为 receive 帧，不是 sent 帧）。
    pong = await driver.next_frame()
    assert pong.frame.event_type == "pong"
    assert driver.terminal_reason is None
    await driver.aclose()
