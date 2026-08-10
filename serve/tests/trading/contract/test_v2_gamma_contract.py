"""WP-01B Gamma wire contract —— 官方 golden fixture + 纯单测（不访问公网）。

覆盖（任务 §6.1）：正常解析、未知字段→raw_extra、JSON string array 二次解析、
数组长度/顺序、价格 string/number→Decimal、缺字段/错误类型 fail-closed、
offset 拒绝、keyset limit 边界、cursor 单调链、429/425/5xx/timeout 重试、
secret redaction、空响应/malformed JSON。
"""

import json
from decimal import Decimal

import httpx
import pytest

from app.schemas.polymarket.common import (
    PolymarketError,
    REASON_HTTP_4XX,
    REASON_HTTP_5XX,
    REASON_MALFORMED_JSON,
    REASON_READ_TIMEOUT,
    REASON_RESPONSE_SCHEMA,
    REASON_TOO_MANY_REQUESTS,
)
from app.schemas.polymarket.gamma import (
    GAMMA_EVENTS_PAGE_LIMIT,
    GAMMA_MARKETS_PAGE_LIMIT,
    GammaMarket,
    assess_binary_market,
    parse_gamma_keyset_page,
)
from app.services.polymarket.base import WirePolicy
from app.services.polymarket.gamma_driver import GammaDriver
from tests.trading.fixtures.poly_fixtures import load_fixture, load_raw_fixture

EVENTS_PAGE_1 = load_fixture("gamma_events_keyset_page_1.json")
EVENTS_PAGE_FINAL = load_fixture("gamma_events_keyset_page_final.json")
MARKETS_PAGE_1 = load_fixture("gamma_markets_keyset_page_1.json")
MARKET_DETAIL = load_fixture("gamma_market_detail.json")
EVENT_DETAIL = load_fixture("gamma_event_detail.json")


def _transport_handler(handler):
    """构造 MockTransport，handler 返回 httpx.Response 或抛异常。"""
    return httpx.MockTransport(handler)


def _driver(handler):
    policy = WirePolicy(
        connect_timeout_s=0.5,
        read_timeout_s=0.5,
        max_retries=2,
        base_backoff_s=0.01,
        max_backoff_s=0.02,
        jitter_s=0.0,
        rate_per_second=1000,
        rate_burst=1000,
    )
    transport = _transport_handler(handler)
    return GammaDriver("https://gamma.example", policy=policy, transport=transport)


# ---------------- 解析 ----------------

def test_parse_golden_events_page():
    page = parse_gamma_keyset_page(EVENTS_PAGE_1, items_key="events")
    items, next_cursor = page
    assert next_cursor == "cursor-2000"
    assert len(items) == 1
    ev = items[0]
    assert ev["id"] == "evt-1001"
    assert ev["slug"] == "will-trump-win-2024"


def test_parse_golden_markets_page_normalized():
    items, next_cursor = parse_gamma_keyset_page(MARKETS_PAGE_1, items_key="markets")
    assert next_cursor == "cursor-9000"
    market = GammaMarket.model_validate(items[0])
    assert market.id == "mkt-5001"
    assert market.condition_id == "0x1234567890abcdef1234567890abcdef12345678"
    assert market.clob_token_ids == ["99887766554433221100", "99887766554433221101"]
    assert market.outcomes == ["Yes", "No"]
    assert market.outcome_prices == [Decimal("0.53"), Decimal("0.47")]
    assert market.accepting_orders is True
    assert market.last_trade_price == Decimal("0.53")


def test_gamma_string_array_and_real_array_both_parse():
    # JSON string array
    m1 = GammaMarket.model_validate(MARKETS_PAGE_1["markets"][0])
    assert m1.clob_token_ids == ["99887766554433221100", "99887766554433221101"]
    # 真实数组（detail fixture）
    m2 = GammaMarket.model_validate(MARKET_DETAIL)
    assert m2.clob_token_ids == ["99887766554433221102", "99887766554433221103"]
    assert m2.outcomes == ["Yes", "No"]


def test_unknown_fields_kept_in_raw_extra():
    m = GammaMarket.model_validate({**MARKET_DETAIL, "someFutureField": {"x": 1}, "futureFlat": "v"})
    assert m.raw_extra["someFutureField"] == {"x": 1}
    assert m.raw_extra["futureFlat"] == "v"
    # 已知字段不进 raw_extra
    assert "question" not in m.raw_extra
    assert "active" not in m.raw_extra


def test_prices_string_or_number_to_decimal():
    # number 价格经 Driver parse_float=Decimal 变为 Decimal；string 价格直接转
    import json as _json

    from app.services.polymarket.base import parse_json_bytes

    raw = _json.dumps(
        {**MARKET_DETAIL, "bestBid": 0.61, "bestAsk": "0.64", "outcomePrices": [0.62, "0.38"]}
    ).encode()
    parsed = parse_json_bytes(raw)
    m = GammaMarket.model_validate(parsed)
    assert m.best_bid == Decimal("0.61")
    assert m.best_ask == Decimal("0.64")
    assert m.outcome_prices == [Decimal("0.62"), Decimal("0.38")]


def test_known_type_error_fails_closed():
    # clobTokenIds 是 JSON 字符串数组；给一个对象 → fail-closed
    with pytest.raises(Exception):
        GammaMarket.model_validate({**MARKET_DETAIL, "clobTokenIds": {"0": "x"}})
    # outcomes 含非字符串元素 → 拒绝
    with pytest.raises(Exception):
        GammaMarket.model_validate({**MARKET_DETAIL, "outcomes": ["Yes", 2]})
    # bool 价格拒绝
    with pytest.raises(Exception):
        GammaMarket.model_validate({**MARKET_DETAIL, "bestBid": True})


def test_float_price_rejected_even_if_provided():
    with pytest.raises(Exception, match="float_forbidden"):
        GammaMarket.model_validate({**MARKET_DETAIL, "bestBid": 0.61})


def test_missing_next_cursor_means_terminal():
    page = parse_gamma_keyset_page(EVENTS_PAGE_FINAL, items_key="events")
    items, next_cursor = page
    assert next_cursor is None
    assert len(items) == 1


def test_next_cursor_known_type_error_fails_closed():
    with pytest.raises(ValueError, match="next_cursor_invalid_type"):
        parse_gamma_keyset_page(
            {"events": [], "next_cursor": 123}, items_key="events"
        )


def test_question_id_accepts_current_official_capitalization():
    market = GammaMarket.model_validate({"id": "1", "questionID": "qid-1"})
    assert market.question_id == "qid-1"


def test_keyset_items_missing_or_wrong_type():
    with pytest.raises(ValueError, match="keyset_missing"):
        parse_gamma_keyset_page({"foo": []}, items_key="events")
    with pytest.raises(ValueError, match="not_array"):
        parse_gamma_keyset_page({"events": {"id": "x"}, "next_cursor": "c"}, items_key="events")


# ---------------- 二元市场解析态 ----------------

def test_assess_binary_complete():
    st = assess_binary_market(["Yes", "No"], ["t0", "t1"], [Decimal("0.5"), Decimal("0.5")])
    assert st.complete and st.reason is None


def test_assess_binary_incomplete_when_empty():
    st = assess_binary_market([], [], [])
    assert not st.complete and st.reason == "mapping_incomplete"


def test_assess_binary_partial_is_incomplete():
    # 1 label + 1 token → 部分映射 → INCOMPLETE（不是 conflict）
    st = assess_binary_market(["Yes"], ["t0"], [])
    assert not st.complete and st.reason == "mapping_incomplete"
    # labels/tokens alone are not a complete three-array binding.
    no_prices = assess_binary_market(["Yes", "No"], ["t0", "t1"], [])
    assert not no_prices.complete and no_prices.reason == "mapping_incomplete"


def test_assess_binary_oversize_is_length_mismatch():
    # 3 labels + 3 tokens → 超二元 → LENGTH_MISMATCH（conflict）
    st = assess_binary_market(["Yes", "No", "Maybe"], ["t0", "t1", "t2"], [])
    assert not st.complete and st.reason == "mapping_length_mismatch"
    # 2+2 但 3 个价格 → LENGTH_MISMATCH
    st2 = assess_binary_market(["Yes", "No"], ["t0", "t1"], [Decimal("0.5"), Decimal("0.5"), Decimal("0.5")])
    assert not st2.complete and st2.reason == "mapping_length_mismatch"


def test_assess_binary_label_conflict():
    st = assess_binary_market(["Yes", "Yes"], ["t0", "t1"], [Decimal("0.5"), Decimal("0.5")])
    assert not st.complete and st.reason == "mapping_label_conflict"


def test_assess_binary_duplicate_token_conflict():
    st = assess_binary_market(
        ["Yes", "No"], ["same", "same"], [Decimal("0.5"), Decimal("0.5")]
    )
    assert not st.complete and st.reason == "mapping_token_conflict"


# ---------------- Driver：offset / limit / cursor ----------------

def test_driver_never_sends_offset_and_uses_keyset_cursor():
    seen_requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append((request.url.path, dict(request.url.params)))
        assert "offset" not in request.url.params
        assert "page" not in request.url.params
        if request.url.params.get("after_cursor") == "cursor-2000":
            return httpx.Response(200, json=EVENTS_PAGE_FINAL)
        return httpx.Response(200, json=EVENTS_PAGE_1)

    driver = _driver(handler)
    import asyncio

    r1 = asyncio.run(driver.keyset_events(cursor=None, limit=500))
    assert r1.typed.next_cursor == "cursor-2000"
    r2 = asyncio.run(driver.keyset_events(cursor=r1.typed.next_cursor, limit=500))
    assert r2.typed.next_cursor is None
    assert len(seen_requests) == 2
    assert all("offset" not in params for _, params in seen_requests)
    assert seen_requests[0][1]["limit"] == "500"
    assert seen_requests[0][1]["closed"] == "false"
    assert seen_requests[1][1]["after_cursor"] == "cursor-2000"


def test_keyset_limit_bounds():
    driver = _driver(lambda req: httpx.Response(200, json=EVENTS_PAGE_FINAL))
    import asyncio

    with pytest.raises(ValueError):
        asyncio.run(driver.keyset_events(limit=0))
    with pytest.raises(ValueError):
        asyncio.run(driver.keyset_events(limit=GAMMA_EVENTS_PAGE_LIMIT + 1))
    with pytest.raises(ValueError):
        asyncio.run(driver.keyset_markets(limit=GAMMA_MARKETS_PAGE_LIMIT + 1))
    with pytest.raises(ValueError):
        asyncio.run(driver.keyset_events(limit=True))


# ---------------- Driver：重试 / 状态码 / timeout / 脱敏 ----------------

def test_driver_retries_429_then_succeeds_with_two_receipts():
    calls = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": "slow down"})
        return httpx.Response(200, json=EVENTS_PAGE_FINAL)

    import asyncio

    driver = _driver(handler)
    result = asyncio.run(driver.keyset_events())
    assert calls["n"] == 2
    assert len(result.receipts) == 2
    assert result.receipts[0].http_status == 429
    assert result.receipts[1].http_status == 200


def test_driver_5xx_exhausts_retries():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "unavailable"})

    import asyncio

    driver = _driver(handler)
    with pytest.raises(PolymarketError) as ei:
        asyncio.run(driver.keyset_events())
    assert ei.value.reason_code == REASON_HTTP_5XX
    assert ei.value.http_status == 503
    assert len(ei.value.receipts) == 3
    assert all(receipt.http_status == 503 for receipt in ei.value.receipts)


def test_driver_4xx_does_not_retry():
    calls = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, json={"error": "bad request"})

    import asyncio

    driver = _driver(handler)
    with pytest.raises(PolymarketError) as ei:
        asyncio.run(driver.keyset_events())
    assert ei.value.reason_code == REASON_HTTP_4XX
    assert calls["n"] == 1


def test_driver_read_timeout_maps_to_reason():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("boom")

    import asyncio

    driver = _driver(handler)
    with pytest.raises(PolymarketError) as ei:
        asyncio.run(driver.keyset_events())
    assert ei.value.reason_code == REASON_READ_TIMEOUT


def test_receipt_redacts_sensitive_headers_and_hashes_body():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=EVENTS_PAGE_FINAL)

    import asyncio

    driver = _driver(handler)
    result = asyncio.run(driver.get_json("/events/keyset", headers={"Authorization": "Bearer sekrit", "X-Trace": "abc"}))
    receipt = result.receipts[0]
    assert "authorization" in receipt.redacted_header_names
    assert "x-trace" not in receipt.redacted_header_names
    # receipt 不包含任何 secret 原文
    import json as _json

    blob = _json.dumps(receipt.__dict__)
    assert "sekrit" not in blob


def test_malformed_json_and_empty_body():
    async def malformed(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"{not json")

    async def empty(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"")

    import asyncio

    with pytest.raises(PolymarketError) as e1:
        asyncio.run(_driver(malformed).keyset_events())
    assert e1.value.reason_code == REASON_MALFORMED_JSON
    assert len(e1.value.receipts) == 1

    from app.schemas.polymarket.common import REASON_EMPTY_RESPONSE

    with pytest.raises(PolymarketError) as e2:
        asyncio.run(_driver(empty).keyset_events())
    assert e2.value.reason_code == REASON_EMPTY_RESPONSE
    assert len(e2.value.receipts) == 1


def test_known_schema_failure_keeps_http_receipt():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"events": [{"id": {"bad": "type"}}]})

    import asyncio

    with pytest.raises(PolymarketError) as exc:
        asyncio.run(_driver(handler).keyset_events())
    assert exc.value.reason_code == REASON_RESPONSE_SCHEMA
    assert len(exc.value.receipts) == 1
    assert exc.value.receipts[0].http_status == 200


def test_nan_infinity_rejected_by_json_parser():
    from app.services.polymarket.base import parse_json_bytes

    with pytest.raises(PolymarketError, match="malformed"):
        parse_json_bytes(b'{"price": NaN}')
