"""Polymarket public Market WebSocket wire schemas.

The public market channel discriminates frames with ``event_type`` (not
``type``).  Provider timestamps and hashes are evidence only; neither is a
resumable sequence.  Unknown or invalid frames are retained verbatim.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import Field, TypeAdapter, field_validator, model_validator

from app.schemas.polymarket.clob_public import ClobBookLevel
from app.schemas.polymarket.common import DecimalPrice, DecimalSize, PolymarketModel


class MarketWsFrameBase(PolymarketModel):
    """Common raw-wire fields.

    ``type`` remains a read-only compatibility view for the existing ingest
    logic; it is never used to parse provider frames.
    """

    event_type: str
    timestamp: str | int | None = None
    raw_text: str = Field(default="", exclude=True)

    @field_validator("timestamp", mode="before")
    @classmethod
    def _timestamp(cls, value: Any) -> str | int | None:
        if value is None:
            return None
        if isinstance(value, bool) or isinstance(value, float):
            raise ValueError("timestamp_invalid_type")
        if isinstance(value, int):
            if value < 0:
                raise ValueError("timestamp_negative")
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return value.strip()
        raise ValueError("timestamp_invalid_type")

    @model_validator(mode="before")
    @classmethod
    def _legacy_type_constructor(cls, value: Any) -> Any:
        # Internal fixtures created these models with ``type=`` before the wire
        # contract was corrected.  Accept that constructor spelling without
        # accepting it in ``parse_market_ws_frame``.
        if isinstance(value, dict) and "event_type" not in value and "type" in value:
            value = dict(value)
            value["event_type"] = value.pop("type")
        return value

    @property
    def type(self) -> str:
        return self.event_type


class MarketWsBook(MarketWsFrameBase):
    event_type: Literal["book"]
    market: str | None = None
    asset_id: str | None = None
    hash: str | None = None
    bids: list[ClobBookLevel] = Field(default_factory=list)
    asks: list[ClobBookLevel] = Field(default_factory=list)

    @field_validator("bids", "asks", mode="before")
    @classmethod
    def _levels(cls, value: Any) -> list[ClobBookLevel]:
        if not isinstance(value, list):
            raise ValueError("levels_not_array")
        return [ClobBookLevel.model_validate(item) for item in value]


class MarketWsPriceChangeLevel(PolymarketModel):
    """One official ``price_changes`` entry.

    Empty defaults preserve direct construction by the local book-state unit
    tests.  The raw parser below requires every official field.
    """

    asset_id: str = ""
    price: DecimalPrice
    size: DecimalSize
    side: Literal["bid", "ask"]
    hash: str = ""
    best_bid: DecimalPrice | None = None
    best_ask: DecimalPrice | None = None

    @field_validator("side", mode="before")
    @classmethod
    def _side(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("price_change_side_invalid")
        side = value.strip()
        if side.upper() in {"BUY", "SELL"}:
            return "bid" if side.upper() == "BUY" else "ask"
        if side.lower() in {"bid", "ask"}:
            return side.lower()
        raise ValueError("price_change_side_invalid")


class MarketWsPriceChange(MarketWsFrameBase):
    event_type: Literal["price_change"]
    market: str | None = None
    price_changes: list[MarketWsPriceChangeLevel] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _legacy_changes_constructor(cls, value: Any) -> Any:
        if isinstance(value, dict) and "price_changes" not in value and "changes" in value:
            value = dict(value)
            value["price_changes"] = value.pop("changes")
        return value

    @field_validator("price_changes", mode="before")
    @classmethod
    def _changes(cls, value: Any) -> list[MarketWsPriceChangeLevel]:
        if not isinstance(value, list):
            raise ValueError("price_changes_not_array")
        return [MarketWsPriceChangeLevel.model_validate(item) for item in value]

    @property
    def changes(self) -> list[MarketWsPriceChangeLevel]:
        return self.price_changes


class MarketWsLastTradePrice(MarketWsFrameBase):
    event_type: Literal["last_trade_price"]
    market: str | None = None
    asset_id: str | None = None
    price: DecimalPrice | None = None
    side: Literal["BUY", "SELL"] | None = None
    size: DecimalSize | None = None
    fee_rate_bps: str | int | None = None
    transaction_hash: str | None = None


class MarketWsTickSizeChange(MarketWsFrameBase):
    event_type: Literal["tick_size_change"]
    market: str | None = None
    asset_id: str | None = None
    old_tick_size: DecimalPrice | None = None
    new_tick_size: DecimalPrice | None = None
    # Kept only for the current internal aggregate state constructor.  It is
    # not an official Market WS field and is not required by the raw parser.
    new_minimum_order_size: DecimalSize | None = None

    @model_validator(mode="before")
    @classmethod
    def _legacy_new_tick_constructor(cls, value: Any) -> Any:
        if isinstance(value, dict) and "new_tick_size" not in value and "new_tick" in value:
            value = dict(value)
            value["new_tick_size"] = value.pop("new_tick")
        return value

    @property
    def new_tick(self) -> DecimalPrice | None:
        return self.new_tick_size


class MarketWsBestBidAsk(MarketWsFrameBase):
    event_type: Literal["best_bid_ask"]
    market: str | None = None
    asset_id: str | None = None
    best_bid: DecimalPrice | None = None
    best_ask: DecimalPrice | None = None
    spread: DecimalPrice | None = None


class MarketWsNewMarket(MarketWsFrameBase):
    event_type: Literal["new_market"]
    id: str | None = None
    market: str | None = None
    question: str | None = None
    slug: str | None = None
    description: str | None = None
    assets_ids: list[str] = Field(default_factory=list)
    outcomes: list[str] = Field(default_factory=list)
    event_message: dict[str, Any] | None = None
    tags: list[str] | None = None
    condition_id: str | None = None
    active: bool | None = None
    clob_token_ids: list[str] | None = None
    sports_market_type: str | None = None
    line: Decimal | None = None
    game_start_time: str | None = None
    order_price_min_tick_size: DecimalPrice | None = None
    group_item_title: str | None = None
    taker_base_fee: Decimal | None = None
    fees_enabled: bool | None = None
    fee_schedule: dict[str, Any] | None = None

    @property
    def market_id(self) -> str | None:
        return self.market


class MarketWsResolved(MarketWsFrameBase):
    event_type: Literal["market_resolved"]
    id: str | None = None
    market: str | None = None
    question: str | None = None
    slug: str | None = None
    description: str | None = None
    assets_ids: list[str] = Field(default_factory=list)
    outcomes: list[str] | None = None
    winning_asset_id: str | None = None
    winning_outcome: str | None = None
    event_message: dict[str, Any] | None = None
    tags: list[str] | None = None

    @property
    def condition_id(self) -> str | None:
        return self.market


class MarketWsPong(MarketWsFrameBase):
    event_type: Literal["pong"]


class MarketWsUnknown(MarketWsFrameBase):
    event_type: Literal["unknown"]
    parse_error: str | None = None


MarketWsFrame = Annotated[
    MarketWsBook
    | MarketWsPriceChange
    | MarketWsLastTradePrice
    | MarketWsTickSizeChange
    | MarketWsBestBidAsk
    | MarketWsNewMarket
    | MarketWsResolved
    | MarketWsPong
    | MarketWsUnknown,
    Field(discriminator="event_type"),
]

_KNOWN_EVENT_TYPES = frozenset(
    {
        "book",
        "price_change",
        "last_trade_price",
        "tick_size_change",
        "best_bid_ask",
        "new_market",
        "market_resolved",
    }
)

_REQUIRED_FIELDS: dict[str, frozenset[str]] = {
    "book": frozenset({"market", "asset_id", "timestamp", "hash", "bids", "asks"}),
    "price_change": frozenset({"market", "price_changes", "timestamp"}),
    "last_trade_price": frozenset(
        {"market", "asset_id", "price", "side", "size", "timestamp"}
    ),
    "tick_size_change": frozenset(
        {"market", "asset_id", "old_tick_size", "new_tick_size", "timestamp"}
    ),
    "best_bid_ask": frozenset(
        {"market", "asset_id", "best_bid", "best_ask", "spread", "timestamp"}
    ),
    "new_market": frozenset(
        {"id", "market", "question", "slug", "assets_ids", "outcomes", "timestamp"}
    ),
    "market_resolved": frozenset(
        {
            "id",
            "market",
            "assets_ids",
            "winning_asset_id",
            "winning_outcome",
            "timestamp",
        }
    ),
}


def _validate_wire_shape(payload: dict[str, Any], event_type: str) -> str | None:
    required_fields = _REQUIRED_FIELDS[event_type]
    missing = required_fields.difference(payload)
    if missing:
        return "missing_fields:" + ",".join(sorted(missing))
    null_fields = {field for field in required_fields if payload.get(field) is None}
    if null_fields:
        return "null_fields:" + ",".join(sorted(null_fields))
    for field in ("market", "asset_id"):
        if field in required_fields and (
            not isinstance(payload[field], str) or not payload[field].strip()
        ):
            return f"invalid_{field}"
    if event_type == "price_change":
        changes = payload["price_changes"]
        if not isinstance(changes, list):
            return "price_changes_not_array"
        required = {"asset_id", "price", "size", "side", "hash", "best_bid", "best_ask"}
        for item in changes:
            if not isinstance(item, dict):
                return "price_change_not_object"
            if required.difference(item):
                return "price_change_missing_fields"
            if any(item.get(field) is None for field in required):
                return "price_change_null_fields"
            if not isinstance(item["asset_id"], str) or not item["asset_id"].strip():
                return "price_change_invalid_asset_id"
    return None


def parse_market_ws_frame(raw_text: str) -> MarketWsFrameBase:
    """Parse one raw frame, preserving invalid or future events verbatim."""
    stripped = raw_text.strip()
    if stripped == "PONG":
        return MarketWsPong(event_type="pong", raw_text=raw_text)
    if not stripped:
        return MarketWsUnknown(
            event_type="unknown", raw_text=raw_text, parse_error="empty_frame"
        )
    try:
        payload = json.loads(
            stripped,
            parse_float=Decimal,
            parse_int=int,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ValueError("nan_infinity")
            ),
        )
    except (json.JSONDecodeError, ValueError):
        return MarketWsUnknown(
            event_type="unknown", raw_text=raw_text, parse_error="malformed_json"
        )
    if not isinstance(payload, dict):
        return MarketWsUnknown(
            event_type="unknown", raw_text=raw_text, parse_error="not_object"
        )

    event_type = payload.get("event_type")
    if event_type not in _KNOWN_EVENT_TYPES:
        return MarketWsUnknown(
            event_type="unknown", raw_text=raw_text, parse_error="unknown_event_type"
        )
    shape_error = _validate_wire_shape(payload, event_type)
    if shape_error is not None:
        return MarketWsUnknown(
            event_type="unknown", raw_text=raw_text, parse_error=shape_error
        )

    try:
        frame = TypeAdapter(MarketWsFrame).validate_python(payload)
    except Exception as exc:  # Pydantic ValidationError
        errors = exc.errors() if hasattr(exc, "errors") else []
        kind = errors[0]["type"] if errors else "invalid"
        return MarketWsUnknown(
            event_type="unknown",
            raw_text=raw_text,
            parse_error=f"validation:{kind}",
        )
    frame.raw_text = raw_text
    return frame
