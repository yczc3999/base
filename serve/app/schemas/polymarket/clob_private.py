"""Private CLOB order wire schemas（WP-05 Checkpoint C）。

只做 Pydantic 解析/规范化，**不发网络请求**。money/price/size 禁 float（复用
``common.DecimalPrice/DecimalSize``）；未知字段进 ``raw_extra``，已知字段类型错误拒绝。

- ``PrivateOrder``：提交参数（token_id/side/price/size/post_only/expiration/salt/timestamp）。
- ``OrderResponse``：``POST /order`` 归一化结果（order_id/status/success/error）。
- ``CancelOrdersResponse``：``DELETE /order`` 逐单结果。
- ``OrderBook``：本仓库冻结的 order book 投影（不做估值判断）。
- ``classify_order_response``：把 wire 响应分类为 ACK/REJECTED/AUTH_STOP/THROTTLED/UNKNOWN。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field, field_validator

from app.schemas.polymarket.common import DecimalPrice, DecimalSize, PolymarketModel


class PrivateOrder(PolymarketModel):
    """一条私有订单提交参数；price/size 全程 Decimal（禁 float）。"""

    token_id: str
    side: Literal["BUY", "SELL"]
    price: DecimalPrice
    size: DecimalSize
    post_only: bool = False
    expiration: int | None = Field(default=None, ge=0)
    salt: int = Field(ge=0)
    timestamp: int = Field(ge=0)

    @field_validator("token_id", mode="before")
    @classmethod
    def _token(cls, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("token_id_required")
        return value.strip()


class OrderResponse(PolymarketModel):
    """``POST /order`` 的归一化响应。``success`` 为 False 时 ``error_msg`` 必填。"""

    order_id: str | None = None
    status: str | None = None
    success: bool = False
    error_msg: str | None = None
    making_amount: DecimalSize | None = None
    taking_amount: DecimalSize | None = None
    trade_ids: tuple[str, ...] = Field(default_factory=tuple)
    transactions_hashes: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("order_id", mode="before")
    @classmethod
    def _order_id(cls, value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError("order_id_invalid")
        return value.strip()

    @field_validator("trade_ids", "transactions_hashes", mode="before")
    @classmethod
    def _tuple_list(cls, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, (list, tuple)):
            return tuple(str(item) for item in value)
        raise ValueError("expected_list")

    @field_validator("success")
    @classmethod
    def _success_bool(cls, value: Any) -> bool:
        if not isinstance(value, bool):
            raise ValueError("success_must_be_bool")
        return value


@dataclass(frozen=True)
class OrderResponseClass:
    """订单响应分类（golden 合同 §response_classification）。"""

    cls: Literal["ACK", "REJECTED", "AUTH_STOP", "THROTTLED", "UNKNOWN"]
    reason: str


def classify_order_response(
    response: OrderResponse | None,
    *,
    http_status: int | None,
    error_code: str | None,
) -> OrderResponseClass:
    """把一次 submit 的响应分类为固定类别。

    - 200 + success → ACK（即使 body 含 order_id 之外字段）。
    - 200 + success=False + error_msg → REJECTED。
    - 400 → REJECTED；401 → AUTH_STOP；425/429 → THROTTLED；5xx/timeout/断连/不可判定 200
      body → UNKNOWN（禁止盲重发）。
    """
    if error_code is not None:
        if error_code == "wire_http_401":
            return OrderResponseClass("AUTH_STOP", error_code)
        if error_code in ("wire_http_425", "wire_http_429"):
            return OrderResponseClass("THROTTLED", error_code)
        if error_code.startswith("wire_http_5"):
            return OrderResponseClass("UNKNOWN", error_code)
        if error_code in (
            "wire_connect_timeout", "wire_read_timeout", "wire_total_timeout",
            "wire_ws_disconnect", "wire_malformed_json", "wire_empty_response",
        ):
            return OrderResponseClass("UNKNOWN", error_code)
        return OrderResponseClass("UNKNOWN", error_code)
    if http_status == 200 and response is not None:
        if response.success:
            return OrderResponseClass("ACK", "ok")
        if response.error_msg:
            return OrderResponseClass("REJECTED", "error_msg")
        return OrderResponseClass("UNKNOWN", "indeterminate_200_body")
    if http_status == 400:
        return OrderResponseClass("REJECTED", "http_400")
    if http_status == 401:
        return OrderResponseClass("AUTH_STOP", "http_401")
    if http_status in (425, 429):
        return OrderResponseClass("THROTTLED", f"http_{http_status}")
    if http_status is not None and http_status >= 500:
        return OrderResponseClass("UNKNOWN", f"http_{http_status}")
    return OrderResponseClass("UNKNOWN", "wire_unreachable")


class CancelItemResult(PolymarketModel):
    """``DELETE /order`` 逐单取消结果。"""

    order_id: str
    ok: bool
    error: str | None = None

    @field_validator("order_id", mode="before")
    @classmethod
    def _order_id(cls, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("order_id_required")
        return value.strip()


class CancelOrdersResponse(PolymarketModel):
    """归一化的取消响应（逐单结果；重复取消收敛为 not_canceled）。"""

    items: list[CancelItemResult] = Field(default_factory=list)
    success: bool = False

    @field_validator("items", mode="before")
    @classmethod
    def _items(cls, value: Any) -> list[CancelItemResult]:
        if isinstance(value, dict):
            value = [value]
        if not isinstance(value, list):
            raise ValueError("cancel_items_not_array")
        return [CancelItemResult.model_validate(item) for item in value]


class OrderBookLevel(PolymarketModel):
    """一层 order book（price/size Decimal，禁 float）。"""

    price: DecimalPrice
    size: DecimalSize


class OrderBook(PolymarketModel):
    """本仓库冻结的 order book 投影（reconcile 时对比 provider 真实状态）。"""

    market: str
    asset_id: str
    bids: list[OrderBookLevel] = Field(default_factory=list)
    asks: list[OrderBookLevel] = Field(default_factory=list)
    timestamp: int | None = None

    @field_validator("market", "asset_id", mode="before")
    @classmethod
    def _required_text(cls, value: Any, info) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{info.field_name}_required")
        return value.strip()

    @field_validator("bids", "asks", mode="before")
    @classmethod
    def _levels(cls, value: Any) -> list[OrderBookLevel]:
        if not isinstance(value, list):
            raise ValueError("levels_not_array")
        return [OrderBookLevel.model_validate(item) for item in value]
