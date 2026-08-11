"""Polymarket Data API REST wire schemas（WP-05 Checkpoint C）。

REST keyset 分页：``{"data": [...], "next_cursor": ...}``。price/size/fee 全程 Decimal
（禁 float）；未知字段进 ``raw_extra``。

- ``DataApiOpenOrder`` / ``DataApiOpenOrders``：账户 open orders。
- ``DataApiTrade`` / ``DataApiTrades``：账户 trades。
- ``DataApiPosition`` / ``DataApiPositions``：账户 positions（reconcile 对比）。
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from app.schemas.polymarket.common import (
    Cursor,
    DecimalPrice,
    DecimalSize,
    PolymarketModel,
)


class _KeysetPageMixin(PolymarketModel):
    data: list[dict[str, Any]] = Field(default_factory=list)
    next_cursor: Cursor = None

    @field_validator("data", mode="before")
    @classmethod
    def _data_list(cls, value: Any) -> list[dict[str, Any]]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("data_not_array")
        return value

    @field_validator("next_cursor", mode="before")
    @classmethod
    def _cursor(cls, value: Any) -> Cursor:
        if value is None or value == "":
            return None
        if isinstance(value, bool) or not isinstance(value, str):
            raise ValueError("next_cursor_invalid")
        return value.strip()


class DataApiOpenOrder(PolymarketModel):
    """一条 open order（provider 或 fixture 观察）。"""

    order_id: str
    token_id: str
    side: str
    price: DecimalPrice
    size: DecimalSize
    original_size: DecimalSize | None = None
    size_matched: DecimalSize | None = None
    status: str | None = None
    created_at: str | None = None

    @field_validator("order_id", "token_id", mode="before")
    @classmethod
    def _required_text(cls, value: Any, info) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{info.field_name}_required")
        return value.strip()

    @field_validator("side", mode="before")
    @classmethod
    def _side(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("side_invalid")
        upper = value.upper()
        if upper not in ("BUY", "SELL"):
            raise ValueError("side_invalid")
        return upper


class DataApiOpenOrders(_KeysetPageMixin):
    @property
    def items(self) -> list[DataApiOpenOrder]:
        return [DataApiOpenOrder.model_validate(item) for item in self.data]

    @property
    def raw_items(self) -> list[dict[str, Any]]:
        return list(self.data)


class DataApiTrade(PolymarketModel):
    """一条成交（provider 或 fixture 观察）。"""

    trade_id: str
    order_id: str | None = None
    token_id: str
    side: str
    price: DecimalPrice
    size: DecimalSize
    fee: DecimalSize | None = None
    matched_at: str | None = None

    @field_validator("trade_id", "token_id", mode="before")
    @classmethod
    def _required_text(cls, value: Any, info) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{info.field_name}_required")
        return value.strip()

    @field_validator("side", mode="before")
    @classmethod
    def _side(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("side_invalid")
        upper = value.upper()
        if upper not in ("BUY", "SELL"):
            raise ValueError("side_invalid")
        return upper


class DataApiTrades(_KeysetPageMixin):
    @property
    def items(self) -> list[DataApiTrade]:
        return [DataApiTrade.model_validate(item) for item in self.data]

    @property
    def raw_items(self) -> list[dict[str, Any]]:
        return list(self.data)


class DataApiPosition(PolymarketModel):
    """一条 position（reconcile 时 provider 真实持仓对比）。"""

    token_id: str
    size: DecimalSize
    avg_price: DecimalPrice | None = None
    market: str | None = None

    @field_validator("token_id", mode="before")
    @classmethod
    def _required_text(cls, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("token_id_required")
        return value.strip()


class DataApiPositions(_KeysetPageMixin):
    @property
    def items(self) -> list[DataApiPosition]:
        return [DataApiPosition.model_validate(item) for item in self.data]

    @property
    def raw_items(self) -> list[dict[str, Any]]:
        return list(self.data)
