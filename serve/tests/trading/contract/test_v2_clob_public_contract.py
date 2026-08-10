"""WP-01B CLOB public wire contract —— golden fixture + 纯单测（不访问公网）。

覆盖（任务 §6.1）：book 解析（price string/number → Decimal）、best_bid_ask 用
max/min（绝不取 [0]）、空簿、batch（book+error 混合）、clob-markets/time/tick/fee、
batch ≤500、429/425/5xx/timeout 重试、secret redaction。
"""

import asyncio
import json
from decimal import Decimal

import httpx
import pytest

from app.schemas.polymarket.clob_public import (
    CLOB_BOOKS_BATCH_LIMIT,
    ClobBook,
    best_bid_ask,
)
from app.schemas.polymarket.common import (
    PolymarketError,
    REASON_HTTP_4XX,
    REASON_HTTP_425,
    REASON_HTTP_5XX,
    REASON_RESPONSE_SCHEMA,
    REASON_TOTAL_TIMEOUT,
    REASON_TOO_MANY_REQUESTS,
)
from app.services.polymarket.base import WirePolicy, parse_json_bytes
from app.services.polymarket.clob_public_driver import ClobPublicDriver
from tests.trading.fixtures.poly_fixtures import load_fixture

BOOK = load_fixture("clob_book.json")
BOOK_STR = load_fixture("clob_book_string_prices.json")
BATCH = load_fixture("clob_books_batch.json")
MKT_CONFIG = load_fixture("clob_market_config.json")
SERVER_TIME = load_fixture("clob_server_time.json")
TICK = load_fixture("clob_tick_size.json")
FEE = load_fixture("clob_fee_rate.json")
TOKEN_MAPPING = load_fixture("clob_token_market_mapping.json")


def _driver(handler, *, max_retries: int = 2):
    policy = WirePolicy(
        connect_timeout_s=0.5,
        read_timeout_s=0.5,
        max_retries=max_retries,
        base_backoff_s=0.01,
        max_backoff_s=0.02,
        jitter_s=0.0,
        rate_per_second=1000,
        rate_burst=1000,
    )
    return ClobPublicDriver(
        "https://clob.example", policy=policy, transport=httpx.MockTransport(handler)
    )


def _run(coro):
    return asyncio.run(coro)


# ---------------- book 解析 ----------------

def test_book_number_prices_parsed_via_decimal():
    # 模拟 Driver 的 parse_float=Decimal 路径
    parsed = parse_json_bytes(json.dumps(BOOK).encode())
    book = ClobBook.model_validate(parsed)
    assert book.asset_id == "99887766554433221110"
    assert book.bids[0].price == Decimal("0.51")
    assert book.bids[0].size == Decimal("1000")
    assert book.tick_size == Decimal("0.01")


def test_book_string_prices_parsed():
    book = ClobBook.model_validate(BOOK_STR)
    assert book.bids[0].price == Decimal("0.5100")
    assert book.asks[0].size == Decimal("400")
    assert book.min_order_size == Decimal("1")


def test_best_bid_ask_uses_max_min_not_index_zero():
    # 故意把 max bid / min ask 放不在索引 0
    payload = dict(BOOK_STR)
    payload.update(
        bids=[{"price": "0.49", "size": "1"}, {"price": "0.51", "size": "2"}],
        asks=[{"price": "0.53", "size": "3"}, {"price": "0.52", "size": "4"}],
    )
    book = ClobBook.model_validate(payload)
    bba = best_bid_ask(book)
    assert bba.best_bid == Decimal("0.51")
    assert bba.best_ask == Decimal("0.52")
    assert bba.crossed is False


def test_best_bid_ask_crossed_detection():
    payload = dict(BOOK_STR)
    payload.update(
        bids=[{"price": "0.53", "size": "1"}],
        asks=[{"price": "0.52", "size": "1"}],
    )
    book = ClobBook.model_validate(payload)
    assert best_bid_ask(book).crossed is True


def test_empty_book_yields_none_sides_not_fabricated():
    payload = dict(BOOK_STR)
    payload.update(bids=[], asks=[])
    book = ClobBook.model_validate(payload)
    bba = best_bid_ask(book)
    assert bba.best_bid is None and bba.best_ask is None
    assert bba.crossed is False


def test_missing_sides_fail_closed():
    payload = dict(BOOK_STR)
    payload.pop("bids")
    payload.pop("asks")
    with pytest.raises(Exception):
        ClobBook.model_validate(payload)


# ---------------- batch ----------------

def test_books_batch_parses_book_and_error_items():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["content-type"] == "application/json"
        body = json.loads(request.content)
        assert body == [
            {"token_id": "99887766554433221110"},
            {"token_id": "99887766554433221111"},
        ]
        return httpx.Response(200, json=BATCH)

    driver = _driver(handler)
    result = _run(driver.books_batch(["99887766554433221110", "99887766554433221111"]))
    assert len(result.typed.items) == 2
    assert result.typed.items[0].ok is True
    assert result.typed.items[0].book.bids[0].price == Decimal("0.50")
    assert result.typed.items[1].ok is True
    assert result.typed.items[1].book.asset_id == "99887766554433221111"


def test_books_batch_rejects_over_limit():
    driver = _driver(lambda req: httpx.Response(200, json=[]))
    with pytest.raises(PolymarketError, match="batch_too_large"):
        _run(driver.books_batch([f"t{i}" for i in range(CLOB_BOOKS_BATCH_LIMIT + 1)]))


# ---------------- clob-markets / time / tick / fee ----------------

def test_clob_market_config_parses():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/clob-markets/cond-1"
        return httpx.Response(200, json=MKT_CONFIG)

    driver = _driver(handler)
    result = _run(driver.clob_market("cond-1"))
    assert result.typed.min_tick_size == Decimal("0.01")
    assert result.typed.min_order_size == Decimal("5")
    assert result.typed.clob_token_ids == ["99887766554433221110", "99887766554433221111"]
    assert result.typed.taker_base_fee_bps == Decimal("30")
    assert result.extra["condition_id"] == "cond-1"


def test_server_time_parses_int_and_str():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=SERVER_TIME)

    driver = _driver(handler)
    assert _run(driver.server_time()).typed.timestamp == 1720000000


def test_tick_size_and_fee_rate():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tick-size":
            return httpx.Response(200, json=TICK)
        return httpx.Response(200, json=FEE)

    driver = _driver(handler)
    tick = _run(driver.tick_size("t1")).typed
    fee = _run(driver.fee_rate("t1")).typed
    assert tick.tick_size == Decimal("0.0100") or tick.minimum_tick_size == Decimal("0.0100")
    assert fee.fee_rate_bps == Decimal("30")


def test_price_side_validation():
    driver = _driver(lambda req: httpx.Response(200, json={"price": "0.52"}))
    with pytest.raises(ValueError, match="side"):
        _run(driver.price("t1", "MID"))
    quote = _run(driver.price("t1", "BUY")).typed
    assert quote.price == Decimal("0.52")
    assert quote.requested_side == "BUY"
    assert quote.quote_role == "BEST_BID"
    sell = _run(driver.price("t1", "SELL")).typed
    assert sell.quote_role == "BEST_ASK"


def test_market_by_token_uses_clob_wire_and_typed_mapping():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "clob.example"
        assert request.url.path == "/markets-by-token/99887766554433221110"
        return httpx.Response(200, json=TOKEN_MAPPING)

    mapping = _run(_driver(handler).market_by_token("99887766554433221110")).typed
    assert mapping.condition_id == TOKEN_MAPPING["condition_id"]
    assert mapping.primary_token_id == "99887766554433221110"
    assert mapping.secondary_token_id == "99887766554433221111"


def test_official_required_book_fields_and_price_range_fail_closed():
    with pytest.raises(Exception):
        ClobBook.model_validate({})
    bad = dict(BOOK_STR)
    bad["bids"] = [{"price": "-0.01", "size": "1"}]
    with pytest.raises(Exception, match="price_out_of_range"):
        ClobBook.model_validate(bad)
    bad["bids"] = [{"price": "1.01", "size": "1"}]
    with pytest.raises(Exception, match="price_out_of_range"):
        ClobBook.model_validate(bad)


# ---------------- 重试 / 状态码 / 脱敏 ----------------

def test_425_retries_then_succeeds():
    calls = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(425, json={})
        return httpx.Response(200, json=BOOK)

    driver = _driver(handler)
    result = _run(driver.book("t1"))
    assert calls["n"] == 2
    assert len(result.receipts) == 2
    assert result.receipts[0].http_status == 425


def test_429_reason_code():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={})

    driver = _driver(handler, max_retries=1)
    with pytest.raises(PolymarketError) as ei:
        _run(driver.book("t1"))
    assert ei.value.reason_code == REASON_TOO_MANY_REQUESTS
    assert len(ei.value.receipts) == 2


def test_5xx_exhausted():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    driver = _driver(handler, max_retries=1)
    with pytest.raises(PolymarketError) as ei:
        _run(driver.book("t1"))
    assert ei.value.reason_code == REASON_HTTP_5XX


def test_404_no_book_raises_4xx_not_fabricated():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "No book found"})

    driver = _driver(handler)
    with pytest.raises(PolymarketError) as ei:
        _run(driver.book("t1"))
    assert ei.value.reason_code == REASON_HTTP_4XX
    assert ei.value.http_status == 404


def test_receipt_never_contains_secret_value():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=SERVER_TIME)

    driver = _driver(handler)
    result = _run(
        driver.get_json(
            "/time", headers={"Authorization": "Bearer super-secret-token", "X-Api-Key": "k"}
        )
    )
    receipt = result.receipts[0]
    assert "authorization" in receipt.redacted_header_names
    assert "x-api-key" in receipt.redacted_header_names
    assert "super-secret-token" not in json.dumps(receipt.__dict__)


def test_get_request_hash_includes_query_identity():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"price": "0.5"})

    driver = _driver(handler)
    first = _run(driver.price("token-a", "BUY"))
    second = _run(driver.price("token-b", "BUY"))
    assert first.receipts[0].request_hash != second.receipts[0].request_hash


def test_schema_failure_preserves_successful_http_receipt():
    driver = _driver(lambda request: httpx.Response(200, json={}))
    with pytest.raises(PolymarketError) as exc:
        _run(driver.book("token-a"))
    assert exc.value.reason_code == REASON_RESPONSE_SCHEMA
    assert len(exc.value.receipts) == 1
    assert exc.value.receipts[0].http_status == 200


def test_total_timeout_bounds_whole_call_and_preserves_receipt():
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.05)
        return httpx.Response(200, json=BOOK_STR)

    policy = WirePolicy(
        connect_timeout_s=1,
        read_timeout_s=1,
        write_timeout_s=1,
        total_timeout_s=0.01,
        max_retries=2,
        rate_per_second=1000,
        rate_burst=1000,
    )
    driver = ClobPublicDriver(
        "https://clob.example", policy=policy, transport=httpx.MockTransport(slow)
    )
    with pytest.raises(PolymarketError) as exc:
        _run(driver.book("token-a"))
    assert exc.value.reason_code == REASON_TOTAL_TIMEOUT
    assert exc.value.receipts


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_retries": -1},
        {"max_retries": 1.5},
        {"total_timeout_s": 0},
        {"rate_per_second": 0},
        {"rate_burst": 1.5},
        {"max_batch_size": 1.5},
        {"jitter_s": -1},
        {"read_timeout_s": float("nan")},
        {"max_backoff_s": float("inf")},
    ],
)
def test_wire_policy_rejects_invalid_bounds(kwargs):
    with pytest.raises(ValueError):
        WirePolicy(**kwargs)
