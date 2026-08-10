"""WP-01B Market WS wire contract —— 本地 websockets server（不访问公网）。

覆盖（任务 §6.1）：官方 event_type、订阅契约、PING/PONG、book/price_change/last_trade/tick_size_change/
best_bid_ask/new_market/resolved 判别联合、未知/malformed 帧保留原文、断线 →
wire_ws_disconnect、PONG 超时 → wire_ws_pong_timeout、本地 receive_seq 递增
（不把 timestamp/hash 当 sequence）。
"""

import asyncio
import json

import pytest
import websockets

from app.schemas.polymarket.common import (
    PolymarketError,
    REASON_WS_DISCONNECT,
    REASON_WS_PONG_TIMEOUT,
)
from app.schemas.polymarket.market_ws import (
    MarketWsBestBidAsk,
    MarketWsBook,
    MarketWsLastTradePrice,
    MarketWsNewMarket,
    MarketWsPong,
    MarketWsPriceChange,
    MarketWsResolved,
    MarketWsTickSizeChange,
    MarketWsUnknown,
    parse_market_ws_frame,
)
from app.services.polymarket.market_ws_driver import (
    MarketWsDriver,
    MarketWsPolicy,
    PING_TEXT,
)
from tests.trading.fixtures.poly_fixtures import load_fixture

BOOK = load_fixture("market_ws_book.json")
PRICE_CHANGE = load_fixture("market_ws_price_change.json")
LAST_TRADE = load_fixture("market_ws_last_trade_price.json")
TICK_CHANGE = load_fixture("market_ws_tick_size_change.json")
BEST_BID_ASK = load_fixture("market_ws_best_bid_ask.json")
NEW_MARKET = load_fixture("market_ws_new_market.json")
RESOLVED = load_fixture("market_ws_resolved.json")


def _uri(server) -> str:
    host, port = server.sockets[0].getsockname()[:2]
    return f"ws://{host}:{port}"


async def _scripted_server(handler):
    """启动一个本地 WS server：先收订阅，再执行 handler(websocket)。"""

    async def ws_handler(websocket):
        subscribe = json.loads(await websocket.recv())
        await handler(websocket, subscribe)

    server = await websockets.serve(ws_handler, "127.0.0.1", 0)
    return server


@pytest.mark.asyncio
async def test_subscribe_then_frames_then_close():
    sent_subscribe = {}
    pings = []
    subscribed = asyncio.Event()

    async def server_handler(websocket, subscribe):
        sent_subscribe.update(subscribe)
        subscribed.set()
        assert subscribe["type"] == "market"
        assert subscribe["assets_ids"] == ["99887766554433221110"]
        assert subscribe["custom_feature_enabled"] is True
        assert "initial_dump" not in subscribe
        assert "level" not in subscribe
        await websocket.send(json.dumps(BOOK))
        await websocket.send(json.dumps(PRICE_CHANGE))
        await websocket.send(json.dumps(LAST_TRADE))
        await websocket.send(json.dumps(TICK_CHANGE))
        await websocket.send(json.dumps(BEST_BID_ASK))
        await websocket.send(json.dumps(NEW_MARKET))
        await websocket.send(json.dumps(RESOLVED))
        await websocket.send("PONG")
        # 监听 PING（后台）
        while True:
            try:
                msg = await asyncio.wait_for(websocket.recv(), timeout=0.5)
                pings.append(msg)
            except asyncio.TimeoutError:
                break
            except websockets.ConnectionClosed:
                break
        await websocket.close()

    server = await _scripted_server(server_handler)
    policy = MarketWsPolicy(
        connect_timeout_s=2.0,
        recv_timeout_s=1.0,
        ping_interval_s=0.1,
        pong_timeout_s=5.0,
    )
    driver = MarketWsDriver(
        _uri(server),
        ["99887766554433221110"],
        policy=policy,
        ws_connect=lambda: websockets.connect(_uri(server)),
    )
    try:
        await driver.connect()
        await asyncio.wait_for(subscribed.wait(), timeout=3.0)
        assert sent_subscribe["type"] == "market"

        msg = await driver.next_frame()
        assert msg.receive_seq == 1
        assert isinstance(msg.frame, MarketWsBook)
        assert msg.frame.event_type == "book"
        assert msg.frame.type == "book"  # runtime compatibility view
        assert msg.frame.asset_id == "99887766554433221110"
        assert msg.frame.raw_text == msg.raw_text
        assert msg.receipts and msg.receipts[0].error_code is None

        msg = await driver.next_frame()
        assert msg.receive_seq == 2
        assert isinstance(msg.frame, MarketWsPriceChange)
        assert msg.frame.changes[0].size == 0  # size=0 → 删除档
        assert msg.frame.price_changes[0].asset_id == "99887766554433221110"
        assert msg.frame.price_changes[0].side == "bid"
        assert msg.frame.price_changes[0].hash
        assert msg.frame.price_changes[0].best_bid is not None
        assert msg.frame.price_changes[0].best_ask is not None

        msg = await driver.next_frame()
        assert isinstance(msg.frame, MarketWsLastTradePrice)
        assert msg.frame.side == "BUY"

        msg = await driver.next_frame()
        assert isinstance(msg.frame, MarketWsTickSizeChange)
        assert msg.frame.new_tick_size is not None
        assert msg.frame.new_tick == msg.frame.new_tick_size

        msg = await driver.next_frame()
        assert isinstance(msg.frame, MarketWsBestBidAsk)

        msg = await driver.next_frame()
        assert isinstance(msg.frame, MarketWsNewMarket)
        assert msg.frame.id == "123456"
        assert msg.frame.market_id == NEW_MARKET["market"]
        assert msg.frame.assets_ids == NEW_MARKET["assets_ids"]

        msg = await driver.next_frame()
        assert isinstance(msg.frame, MarketWsResolved)
        assert msg.frame.condition_id == RESOLVED["market"]
        assert msg.frame.winning_asset_id == RESOLVED["winning_asset_id"]

        msg = await driver.next_frame()
        assert isinstance(msg.frame, MarketWsPong)
        assert msg.receive_seq == 8

        # 给 PING 任务留出时间发送；server 的 recv 循环窗口 0.5s
        await asyncio.sleep(0.3)
    finally:
        await driver.aclose()
        server.close()
        await server.wait_closed()

    assert PING_TEXT in pings


@pytest.mark.asyncio
async def test_unknown_and_malformed_frames_kept_raw():
    async def server_handler(websocket, subscribe):
        await websocket.send(json.dumps({"event_type": "something_new", "data": 1}))
        await websocket.send("this is not json")
        await websocket.close()

    server = await _scripted_server(server_handler)
    driver = MarketWsDriver(
        _uri(server),
        ["t1"],
        policy=MarketWsPolicy(connect_timeout_s=2.0, recv_timeout_s=1.0, ping_interval_s=10.0, pong_timeout_s=5.0),
        ws_connect=lambda: websockets.connect(_uri(server)),
    )
    try:
        await driver.connect()
        m1 = await driver.next_frame()
        assert isinstance(m1.frame, MarketWsUnknown)
        assert m1.frame.parse_error == "unknown_event_type"
        assert "something_new" in m1.raw_text

        m2 = await driver.next_frame()
        assert isinstance(m2.frame, MarketWsUnknown)
        assert m2.frame.parse_error == "malformed_json"
        assert m2.raw_text == "this is not json"
    finally:
        await driver.aclose()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_disconnect_raises_ws_disconnect():
    async def server_handler(websocket, subscribe):
        await websocket.close()

    server = await _scripted_server(server_handler)
    driver = MarketWsDriver(
        _uri(server),
        ["t1"],
        policy=MarketWsPolicy(connect_timeout_s=2.0, recv_timeout_s=1.0, ping_interval_s=10.0, pong_timeout_s=5.0),
        ws_connect=lambda: websockets.connect(_uri(server)),
    )
    try:
        await driver.connect()
        with pytest.raises(PolymarketError) as ei:
            await driver.next_frame()
        assert ei.value.reason_code == REASON_WS_DISCONNECT
        assert ei.value.receipts
        assert ei.value.receipts[-1].error_code == REASON_WS_DISCONNECT
    finally:
        await driver.aclose()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_pong_timeout_fails_closed():
    async def server_handler(websocket, subscribe):
        # 收到订阅后什么都不发（不发 PONG）
        try:
            await asyncio.sleep(3)
        except asyncio.CancelledError:
            pass

    server = await _scripted_server(server_handler)
    driver = MarketWsDriver(
        _uri(server),
        ["t1"],
        policy=MarketWsPolicy(
            connect_timeout_s=2.0,
            recv_timeout_s=0.5,
            ping_interval_s=0.05,
            pong_timeout_s=0.2,
        ),
        ws_connect=lambda: websockets.connect(_uri(server)),
    )
    try:
        await driver.connect()
        with pytest.raises(PolymarketError) as ei:
            await driver.next_frame()
        assert ei.value.reason_code == REASON_WS_PONG_TIMEOUT
        assert ei.value.receipts
        assert ei.value.receipts[-1].error_code == REASON_WS_PONG_TIMEOUT
    finally:
        await driver.aclose()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_continuous_data_cannot_mask_missing_pong():
    """A receive-timeout-only implementation would run forever in this case."""

    async def server_handler(websocket, subscribe):
        for _ in range(100):
            try:
                await websocket.send(json.dumps(BOOK))
                await asyncio.sleep(0.01)
            except websockets.ConnectionClosed:
                return

    server = await _scripted_server(server_handler)
    driver = MarketWsDriver(
        _uri(server),
        ["99887766554433221110"],
        policy=MarketWsPolicy(
            connect_timeout_s=1.0,
            recv_timeout_s=0.5,
            ping_interval_s=0.02,
            pong_timeout_s=0.06,
        ),
        ws_connect=lambda: websockets.connect(_uri(server)),
    )
    try:
        await driver.connect()
        with pytest.raises(PolymarketError) as ei:
            while True:
                await driver.next_frame()
        assert ei.value.reason_code == REASON_WS_PONG_TIMEOUT
        assert ei.value.receipts[-1].error_code == REASON_WS_PONG_TIMEOUT
    finally:
        await driver.aclose()
        server.close()
        await server.wait_closed()


@pytest.mark.parametrize(
    "field,value",
    [
        ("connect_timeout_s", 0),
        ("recv_timeout_s", -1),
        ("ping_interval_s", float("inf")),
        ("pong_timeout_s", True),
    ],
)
def test_policy_requires_positive_finite_values(field, value):
    kwargs = {field: value}
    with pytest.raises(ValueError, match=f"{field}_must_be_positive"):
        MarketWsPolicy(**kwargs)


def test_legacy_type_key_is_not_accepted_as_provider_discriminator():
    raw = json.dumps({"type": "book", "asset_id": "t1", "bids": [], "asks": []})
    frame = parse_market_ws_frame(raw)
    assert isinstance(frame, MarketWsUnknown)
    assert frame.parse_error == "unknown_event_type"


def test_price_change_missing_official_item_fields_is_retained_unknown():
    malformed = dict(PRICE_CHANGE)
    malformed["price_changes"] = [{"price": "0.50", "size": "1", "side": "BUY"}]
    frame = parse_market_ws_frame(json.dumps(malformed))
    assert isinstance(frame, MarketWsUnknown)
    assert frame.parse_error == "price_change_missing_fields"


@pytest.mark.asyncio
async def test_local_receive_seq_not_derived_from_provider_timestamp():
    # 两个帧 timestamp 相同，但 receive_seq 递增 → 证明 seq 是本地顺序，不是上游 sequence
    async def server_handler(websocket, subscribe):
        f1 = dict(BOOK, timestamp=1720000100)
        f2 = dict(PRICE_CHANGE, timestamp=1720000100)
        await websocket.send(json.dumps(f1))
        await websocket.send(json.dumps(f2))
        await websocket.close()

    server = await _scripted_server(server_handler)
    driver = MarketWsDriver(
        _uri(server),
        ["99887766554433221110"],
        policy=MarketWsPolicy(connect_timeout_s=2.0, recv_timeout_s=1.0, ping_interval_s=10.0, pong_timeout_s=5.0),
        ws_connect=lambda: websockets.connect(_uri(server)),
    )
    try:
        await driver.connect()
        a = await driver.next_frame()
        b = await driver.next_frame()
        assert a.frame.timestamp == b.frame.timestamp
        assert a.receive_seq == 1 and b.receive_seq == 2
    finally:
        await driver.aclose()
        server.close()
        await server.wait_closed()
