"""Polymarket User WebSocket wire schemas（WP-05 Checkpoint C）。

User WS 推送账户私有 order/trade 帧。raw private frame 只以脱敏 artifact hash / typed event
落库，**绝不保存 signature/secret 明文**。

- ``UserOrderEvent``：订单状态/价格/大小变化（price/size Decimal）。
- ``UserTradeEvent``：成交事件（price/size/fee Decimal）。
- ``UserWsFrame``：判别联合（order/trade/ping/pong/unknown）。
- ``parse_user_ws_frame``：解析一帧；未知/畸形保留为 ``UserWsUnknown``。
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import Field, TypeAdapter, field_validator, model_validator

from app.schemas.polymarket.common import DecimalPrice, DecimalSize, PolymarketModel


class UserWsFrameBase(PolymarketModel):
    """通用 wire 字段。``event_type`` 区分帧类型。"""

    event_type: str
    raw_text: str = Field(default="", exclude=True)

    @property
    def type(self) -> str:
        return self.event_type


class UserOrderEvent(UserWsFrameBase):
    """账户订单帧（order_id/token_id/side/price/size/status）。"""

    event_type: Literal["order"]
    order_id: str = ""
    token_id: str = ""
    side: Literal["BUY", "SELL"] | None = None
    price: DecimalPrice | None = None
    size: DecimalSize | None = None
    status: str | None = None
    timestamp: int | None = None

    @field_validator("order_id", "token_id", mode="before")
    @classmethod
    def _text(cls, value: Any) -> str:
        return value if isinstance(value, str) else ""

    @field_validator("side", mode="before")
    @classmethod
    def _side(cls, value: Any) -> Literal["BUY", "SELL"] | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("order_side_invalid")
        upper = value.upper()
        if upper not in ("BUY", "SELL"):
            raise ValueError("order_side_invalid")
        return upper


class UserTradeEvent(UserWsFrameBase):
    """账户成交帧（external_trade_id/token_id/side/price/size/fee）。"""

    event_type: Literal["trade"]
    trade_id: str = ""
    order_id: str = ""
    token_id: str = ""
    side: Literal["BUY", "SELL"] | None = None
    price: DecimalPrice | None = None
    size: DecimalSize | None = None
    fee: DecimalSize | None = None
    timestamp: int | None = None

    @field_validator("trade_id", "order_id", "token_id", mode="before")
    @classmethod
    def _text(cls, value: Any) -> str:
        return value if isinstance(value, str) else ""

    @field_validator("side", mode="before")
    @classmethod
    def _side(cls, value: Any) -> Literal["BUY", "SELL"] | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("trade_side_invalid")
        upper = value.upper()
        if upper not in ("BUY", "SELL"):
            raise ValueError("trade_side_invalid")
        return upper


class UserWsPong(UserWsFrameBase):
    event_type: Literal["pong"]


class UserWsUnknown(UserWsFrameBase):
    event_type: Literal["unknown"]
    parse_error: str | None = None


UserWsFrame = Annotated[
    UserOrderEvent | UserTradeEvent | UserWsPong | UserWsUnknown,
    Field(discriminator="event_type"),
]

_KNOWN_EVENT_TYPES = frozenset({"order", "trade"})


def parse_user_ws_frame(raw_text: str) -> UserWsFrameBase:
    """解析一帧 User WS；未知/畸形保留为 ``UserWsUnknown``（含脱敏 raw_text）。"""
    stripped = raw_text.strip()
    if stripped == "PONG":
        return UserWsPong(event_type="pong", raw_text=raw_text)
    if not stripped:
        return UserWsUnknown(event_type="unknown", raw_text=raw_text, parse_error="empty_frame")
    try:
        payload = json.loads(
            stripped,
            parse_float=Decimal,
            parse_int=int,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("nan_infinity")),
        )
    except (json.JSONDecodeError, ValueError):
        return UserWsUnknown(event_type="unknown", raw_text=raw_text, parse_error="malformed_json")
    if not isinstance(payload, dict):
        return UserWsUnknown(event_type="unknown", raw_text=raw_text, parse_error="not_object")
    event_type = payload.get("event_type") or payload.get("type")
    if event_type not in _KNOWN_EVENT_TYPES:
        return UserWsUnknown(
            event_type="unknown", raw_text=raw_text, parse_error="unknown_event_type"
        )
    normalized = dict(payload)
    if "event_type" not in normalized and "type" in normalized:
        normalized["event_type"] = normalized.pop("type")
    try:
        frame = TypeAdapter(UserWsFrame).validate_python(normalized)
    except Exception as exc:  # Pydantic ValidationError
        errors = exc.errors() if hasattr(exc, "errors") else []
        kind = errors[0]["type"] if errors else "invalid"
        return UserWsUnknown(
            event_type="unknown", raw_text=raw_text, parse_error=f"validation:{kind}",
        )
    frame.raw_text = raw_text
    return frame


def user_ws_frame_artifact_hash(frame: UserWsFrameBase) -> str:
    """脱敏 artifact hash：只对 typed 字段（不含 raw_text/signature）做 canonical hash。"""
    import hashlib
    import json as _json

    if isinstance(frame, UserWsUnknown):
        content = {"event_type": "unknown", "parse_error": frame.parse_error}
    else:
        content = frame.model_dump(exclude={"raw_text"}, mode="json")
    canonical = _json.dumps(
        {k: v for k, v in content.items() if v is not None},
        sort_keys=True, separators=(",", ":"), default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
