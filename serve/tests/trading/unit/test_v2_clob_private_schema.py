"""WP-05 Checkpoint C：private CLOB wire schema 单测（Decimal、raw_extra、类型错误拒）。"""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas.polymarket.clob_private import (
    CancelItemResult,
    CancelOrdersResponse,
    OrderBook,
    OrderBookLevel,
    OrderResponse,
    PrivateOrder,
    classify_order_response,
)


def test_private_order_decimal_price_size():
    order = PrivateOrder(
        token_id="tok-1", side="BUY", price="0.55", size="10", salt=1, timestamp=2,
    )
    assert order.price == Decimal("0.55")
    assert order.size == Decimal("10")
    assert order.post_only is False


def test_private_order_rejects_float_price_and_size():
    with pytest.raises(ValidationError):
        PrivateOrder(token_id="tok-1", side="BUY", price=0.55, size=10, salt=1, timestamp=2)
    with pytest.raises(ValidationError):
        PrivateOrder(token_id="tok-1", side="BUY", price="0.55", size=10.5, salt=1, timestamp=2)


def test_private_order_unknown_field_to_raw_extra():
    order = PrivateOrder(
        token_id="tok-1", side="SELL", price="0.30", size="5", salt=1, timestamp=2,
        custom_provider_field="hello",
    )
    assert order.raw_extra == {"custom_provider_field": "hello"}


def test_private_order_side_enum_rejected():
    with pytest.raises(ValidationError):
        PrivateOrder(token_id="t", side="BID", price="0.5", size="1", salt=1, timestamp=2)


def test_private_order_token_required():
    with pytest.raises(ValidationError):
        PrivateOrder(token_id="  ", side="BUY", price="0.5", size="1", salt=1, timestamp=2)


def test_order_response_ack_parse():
    response = OrderResponse(
        order_id="ord-123", status="live", success=True,
        making_amount="0.55", taking_amount="10",
        trade_ids=["t1", "t2"], transactions_hashes=["0xabc"],
    )
    assert response.success is True
    assert response.making_amount == Decimal("0.55")
    assert response.trade_ids == ("t1", "t2")


def test_order_response_float_amounts_rejected():
    with pytest.raises(ValidationError):
        OrderResponse(order_id="o", status="live", success=True, making_amount=0.55)


def test_classify_order_response_matrix():
    assert classify_order_response(None, http_status=200, error_code=None).cls == "UNKNOWN"
    ack = OrderResponse(order_id="o", status="live", success=True)
    assert classify_order_response(ack, http_status=200, error_code=None).cls == "ACK"
    rej = OrderResponse(order_id=None, status="unmatched", success=False, error_msg="bad")
    assert classify_order_response(rej, http_status=200, error_code=None).cls == "REJECTED"
    assert classify_order_response(None, http_status=400, error_code=None).cls == "REJECTED"
    assert classify_order_response(None, http_status=401, error_code=None).cls == "AUTH_STOP"
    assert classify_order_response(None, http_status=425, error_code=None).cls == "THROTTLED"
    assert classify_order_response(None, http_status=429, error_code=None).cls == "THROTTLED"
    assert classify_order_response(None, http_status=500, error_code=None).cls == "UNKNOWN"
    assert classify_order_response(None, http_status=None, error_code="wire_read_timeout").cls == "UNKNOWN"


def test_cancel_orders_response_normalization():
    parsed = CancelOrdersResponse(
        items=[
            CancelItemResult(order_id="a", ok=True),
            CancelItemResult(order_id="b", ok=False, error="not found"),
        ],
        success=False,
    )
    assert len(parsed.items) == 2
    assert parsed.items[1].ok is False


def test_order_book_levels_and_raw_extra():
    book = OrderBook(
        market="m1", asset_id="tok-1",
        bids=[{"price": "0.50", "size": "100", "extra": "x"}],
        asks=[{"price": "0.51", "size": "90"}],
        timestamp=123,
    )
    assert book.bids[0].price == Decimal("0.50")
    assert book.bids[0].raw_extra == {"extra": "x"}
    with pytest.raises(ValidationError):
        OrderBookLevel(price=0.5, size="1")


def test_order_book_float_level_rejected():
    with pytest.raises(ValidationError):
        OrderBook(
            market="m", asset_id="t",
            bids=[{"price": 0.5, "size": "1"}],
            asks=[{"price": "0.51", "size": "1"}],
        )
