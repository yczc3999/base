"""Current Polymarket CLOB public REST wire contracts.

The classes in this module parse provider payloads only.  Public probability
prices are bounded Decimal values; catalog amounts use separate types.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from pydantic import AliasChoices, Field, RootModel, field_validator, model_validator

from app.schemas.polymarket.common import (
    DecimalPrice,
    DecimalRate,
    DecimalSize,
    PolymarketModel,
)

CLOB_BOOKS_BATCH_LIMIT = 500


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field}_required")
    return value.strip()


def _wire_timestamp(value: Any) -> int:
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError("timestamp_invalid_type")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
    else:
        raise ValueError("timestamp_invalid_type")
    if parsed < 0:
        raise ValueError("timestamp_negative")
    return parsed


class ClobBookLevel(PolymarketModel):
    """One aggregated depth level."""

    price: DecimalPrice
    size: DecimalSize


def _level_list(value: Any) -> list[ClobBookLevel]:
    if not isinstance(value, list):
        raise ValueError("levels_not_array")
    return [ClobBookLevel.model_validate(item) for item in value]


class ClobBook(PolymarketModel):
    """Official ``GET /book`` / ``POST /books`` order-book response.

    Fields marked required by the provider remain required here.  An explicitly
    empty ``bids`` or ``asks`` array is valid wire data; an omitted side is a
    schema failure rather than a fabricated empty book.
    """

    market: str
    asset_id: str
    timestamp: int
    hash: str
    bids: list[ClobBookLevel]
    asks: list[ClobBookLevel]
    min_order_size: DecimalSize
    tick_size: DecimalPrice
    neg_risk: bool
    last_trade_price: DecimalPrice

    @field_validator("market", "asset_id", "hash", mode="before")
    @classmethod
    def _v_required_text(cls, value: Any, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("timestamp", mode="before")
    @classmethod
    def _v_timestamp(cls, value: Any) -> int:
        return _wire_timestamp(value)

    @field_validator("bids", "asks", mode="before")
    @classmethod
    def _v_levels(cls, value: Any) -> list[ClobBookLevel]:
        return _level_list(value)

    @field_validator("min_order_size")
    @classmethod
    def _v_min_order_size(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("min_order_size_not_positive")
        return value

    @field_validator("tick_size")
    @classmethod
    def _v_tick_size(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("tick_size_not_positive")
        return value


@dataclass(frozen=True)
class BestBidAsk:
    best_bid: Decimal | None
    best_ask: Decimal | None

    @property
    def crossed(self) -> bool:
        return (
            self.best_bid is not None
            and self.best_ask is not None
            and self.best_bid >= self.best_ask
        )


def best_bid_ask(book: ClobBook) -> BestBidAsk:
    """Compute extrema; never trust provider array order."""
    return BestBidAsk(
        max((level.price for level in book.bids), default=None),
        min((level.price for level in book.asks), default=None),
    )


class ClobBookBatchItem(PolymarketModel):
    """Internal normalized element for a batch book response."""

    ok: bool
    book: ClobBook | None = None
    token_id: str | None = None
    error: str | None = None


class ClobBatchBooksResult(PolymarketModel):
    items: list[ClobBookBatchItem] = Field(default_factory=list)


class ClobMarketToken(PolymarketModel):
    """Compact token entry returned by ``/clob-markets/{condition_id}``."""

    token_id: str = Field(validation_alias="t")
    outcome: str = Field(validation_alias="o")

    @field_validator("token_id", "outcome", mode="before")
    @classmethod
    def _v_text(cls, value: Any, info) -> str:
        return _required_text(value, info.field_name)


class ClobMarketConfig(PolymarketModel):
    """Official compact CLOB market-info response.

    The provider intentionally uses compact keys.  Keeping aliases here avoids
    silently accepting the object into ``raw_extra`` while all typed values are
    ``None``.
    """

    game_start_time: str | None = Field(validation_alias="gst")
    rewards: dict[str, Any] = Field(validation_alias="r")
    tokens: list[ClobMarketToken] = Field(validation_alias="t")
    min_order_size: DecimalSize = Field(validation_alias="mos")
    min_tick_size: DecimalPrice = Field(validation_alias="mts")
    maker_base_fee_bps: DecimalRate = Field(validation_alias="mbf")
    taker_base_fee_bps: DecimalRate = Field(validation_alias="tbf")
    rfq_enabled: bool = Field(validation_alias="rfqe")
    taker_order_delay_enabled: bool = Field(validation_alias="itode")
    blockaid_check_enabled: bool = Field(validation_alias="ibce")
    fee_details: dict[str, Any] = Field(validation_alias="fd")
    min_order_age_s: int = Field(validation_alias="oas")

    @field_validator("tokens", mode="before")
    @classmethod
    def _v_tokens(cls, value: Any) -> Any:
        if not isinstance(value, list):
            raise ValueError("clob_market_tokens_not_array")
        return value

    @field_validator("min_order_size")
    @classmethod
    def _v_min_order_size(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("min_order_size_not_positive")
        return value

    @field_validator("min_tick_size")
    @classmethod
    def _v_min_tick_size(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("tick_size_not_positive")
        return value

    @field_validator("min_order_age_s", mode="before")
    @classmethod
    def _v_order_age(cls, value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("min_order_age_invalid")
        return value

    @property
    def clob_token_ids(self) -> list[str]:
        return [token.token_id for token in self.tokens]


class ClobServerTime(RootModel[int]):
    """Official ``GET /time`` response is a bare Unix timestamp integer."""

    @field_validator("root", mode="before")
    @classmethod
    def _v_root(cls, value: Any) -> int:
        return _wire_timestamp(value)

    @property
    def timestamp(self) -> int:
        return self.root


class ClobPriceQuote(PolymarketModel):
    """Single-token price plus the exact provider-side semantics used."""

    price: DecimalPrice
    requested_side: Literal["BUY", "SELL"]
    quote_role: Literal["BEST_BID", "BEST_ASK"]


class ClobTickSize(PolymarketModel):
    minimum_tick_size: DecimalPrice = Field(
        validation_alias=AliasChoices("minimum_tick_size", "tick_size")
    )

    @field_validator("minimum_tick_size")
    @classmethod
    def _v_tick(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("tick_size_not_positive")
        return value

    @property
    def tick_size(self) -> Decimal:
        return self.minimum_tick_size


class ClobFeeRate(PolymarketModel):
    """``GET /fee-rate`` returns ``base_fee`` in basis points."""

    base_fee: DecimalRate

    @property
    def fee_rate_bps(self) -> Decimal:
        return self.base_fee


class ClobTokenMarketMapping(PolymarketModel):
    """CLOB ``GET /markets-by-token/{token_id}`` response."""

    condition_id: str
    primary_token_id: str
    secondary_token_id: str

    @field_validator(
        "condition_id", "primary_token_id", "secondary_token_id", mode="before"
    )
    @classmethod
    def _v_mapping_text(cls, value: Any, info) -> str:
        return _required_text(value, info.field_name)

    @model_validator(mode="after")
    def _tokens_distinct(self) -> "ClobTokenMarketMapping":
        if self.primary_token_id == self.secondary_token_id:
            raise ValueError("token_mapping_duplicate_tokens")
        return self
