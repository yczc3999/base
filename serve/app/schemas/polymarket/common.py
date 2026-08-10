"""Polymarket 公共 wire 类型（WP-01B Checkpoint A）。

只做 Pydantic 解析与规范化，**不发网络请求**（实施合同 §5.1）。

- Decimal 价格/数量：Driver 以 ``parse_float=Decimal`` 解析 JSON，schema 侧再拒绝 float，
  保证 money/price/size/tick 全程无 float。
- UTC 时间：provider 字符串（含 ``Z``/offset）或 aware datetime 统一为 UTC；
  naive 视为 UTC（记录在案：provider 常见行为，非系统 received_at）。
- 未知字段：``extra="allow"`` 收集到 ``raw_extra``，原始内容由 raw artifact 保留。
- ``ProviderError`` / ``RequestReceipt``：固定 reason code、脱敏，供每次 attempt 持久化。
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Any, Iterable

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, AfterValidator

UTC = timezone.utc

# 可选 keyset cursor（``after_cursor`` 为空串/None 表示从首页开始）
Cursor = str | None


class PolymarketError(Exception):
    """Provider wire 层受控异常：固定 reason code，不含 DSN/secret/provider 原文。"""

    def __init__(
        self,
        reason_code: str,
        *,
        http_status: int | None = None,
        detail: str | None = None,
        receipts: Iterable["RequestReceipt"] = (),
    ) -> None:
        self.reason_code = reason_code
        self.http_status = http_status
        self.detail = detail
        # A failed call is still an observable provider attempt.  Keeping the
        # receipts on the controlled exception prevents retry/HTTP/schema
        # failures from disappearing merely because no DriverCallResult exists.
        self.receipts = tuple(receipts)
        super().__init__(reason_code)


# ---------------- 时间 ----------------

def _parse_utc_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if isinstance(value, str):
        text_val = value.strip()
        if not text_val:
            raise ValueError("empty_datetime")
        # fromisoformat 3.11+ 支持尾部 Z；此处显式归一避免兼容分叉。
        if text_val.endswith("Z"):
            text_val = text_val[:-1] + "+00:00"
        dt = datetime.fromisoformat(text_val)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    raise ValueError("invalid_datetime_type")


ProviderDateTime = Annotated[datetime, BeforeValidator(_parse_utc_datetime)]


# ---------------- Decimal 价格/数量 ----------------

def _reject_float(value: Any) -> Any:
    """拒绝 float/bool 输入；Driver 已用 parse_float=Decimal，此处是 fail-closed 兜底。"""
    if isinstance(value, bool):
        raise ValueError("decimal_bool_forbidden")
    if isinstance(value, float):
        raise ValueError("decimal_float_forbidden")
    return value


def _finite(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise ValueError("decimal_not_finite")
    return value


def _nonneg(value: Decimal) -> Decimal:
    if value < 0:
        raise ValueError("decimal_negative")
    return value


def _probability_price(value: Decimal) -> Decimal:
    if value < 0 or value > 1:
        raise ValueError("price_out_of_range")
    return value


DecimalPrice = Annotated[
    Decimal,
    BeforeValidator(_reject_float),
    AfterValidator(_finite),
    AfterValidator(_probability_price),
]

DecimalSize = Annotated[
    Decimal,
    BeforeValidator(_reject_float),
    AfterValidator(_finite),
    AfterValidator(_nonneg),
]

DecimalRate = Annotated[
    Decimal,
    BeforeValidator(_reject_float),
    AfterValidator(_finite),
    AfterValidator(_nonneg),
]

# Provider amounts such as volume/liquidity are not probability prices and may
# exceed one.  Keep them distinct so tightening order-book prices cannot break
# catalog parsing.
DecimalNonNegative = Annotated[
    Decimal,
    BeforeValidator(_reject_float),
    AfterValidator(_finite),
    AfterValidator(_nonneg),
]

DecimalSigned = Annotated[
    Decimal,
    BeforeValidator(_reject_float),
    AfterValidator(_finite),
]


# ---------------- schema 基类 ----------------

class PolymarketModel(BaseModel):
    """所有 Polymarket wire schema 的基础：允许额外字段并收集到 ``raw_extra``。"""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    raw_extra: dict[str, Any] = Field(default_factory=dict, exclude=True)

    def model_post_init(self, __context: Any) -> None:
        extras = getattr(self, "model_extra", None) or {}
        self.raw_extra = dict(extras)


# ---------------- JSON 字符串数组（Gamma 常见）----------------

def parse_json_string_array(value: Any, field_name: str) -> list[Any]:
    """Gamma 的 outcomes/prices/clobTokenIds 可能是 JSON 字符串数组或真实数组。

    - ``str`` → 解析 JSON；空串 → 空列表；
    - ``list`` → 原样；
    - 其他类型 → fail-closed ``<field>_invalid_type``。
    """
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return []
        try:
            parsed = json.loads(s)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{field_name}_invalid_json_array") from exc
        if not isinstance(parsed, list):
            raise ValueError(f"{field_name}_not_array")
        return parsed
    if isinstance(value, list):
        return value
    raise ValueError(f"{field_name}_invalid_type")


def parse_decimal_array(value: Any, field_name: str) -> list[Decimal]:
    """把字符串/数字数组归一为 Decimal 数组；float 元素 fail-closed。"""
    items = parse_json_string_array(value, field_name)
    out: list[Decimal] = []
    for item in items:
        if isinstance(item, bool):
            raise ValueError(f"{field_name}_bool_forbidden")
        if isinstance(item, float):
            raise ValueError(f"{field_name}_float_forbidden")
        dec = Decimal(str(item))
        if not dec.is_finite():
            raise ValueError(f"{field_name}_not_finite")
        out.append(dec)
    return out


# ---------------- Request receipt ----------------

@dataclass(frozen=True)
class RequestReceipt:
    """一次 HTTP/WS attempt 的脱敏收据；失败也必须生成（任务 §2.7）。

    绝不包含 Authorization/Cookie/signature 等明文；请求/响应只存规范哈希。
    """

    attempt_id: str
    endpoint: str
    method: str
    http_status: int | None
    latency_ms: int
    error_code: str | None
    request_hash: str
    response_hash: str | None
    retry_count: int
    retry_after_s: float | None = None
    redacted_header_names: tuple[str, ...] = ()

    @classmethod
    def new(
        cls,
        *,
        endpoint: str,
        method: str,
        http_status: int | None,
        latency_ms: int,
        error_code: str | None,
        request_body: bytes | None,
        response_body: bytes | None,
        retry_count: int,
        request_fingerprint: bytes | None = None,
        retry_after_s: float | None = None,
        redacted_header_names: tuple[str, ...] = (),
    ) -> "RequestReceipt":
        return cls(
            attempt_id=uuid.uuid4().hex,
            endpoint=endpoint,
            method=method,
            http_status=http_status,
            latency_ms=latency_ms,
            error_code=error_code,
            request_hash=_sha256(
                request_fingerprint
                if request_fingerprint is not None
                else (request_body or b"")
            ),
            response_hash=_sha256(response_body) if response_body is not None else None,
            retry_count=retry_count,
            retry_after_s=retry_after_s,
            redacted_header_names=redacted_header_names,
        )


@dataclass(frozen=True)
class DriverCallResult:
    """Driver 一次调用的 typed 结果 + raw bytes（供 artifact）+ 全部 attempt receipts。"""

    typed: Any
    raw: bytes
    receipts: tuple[RequestReceipt, ...]
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def last_receipt(self) -> RequestReceipt | None:
        return self.receipts[-1] if self.receipts else None


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------- redaction ----------------

_SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "poly-signature",
        "poly-api-key",
        "poly-passphrase",
        "proxy-authorization",
    }
)


def redact_headers(headers: dict[str, str]) -> tuple[str, ...]:
    """返回被脱敏的 header 名（小写，用于 receipt 记录）；值一律不保存。"""
    return tuple(
        sorted(
            name.lower()
            for name in headers
            if name.lower() in _SENSITIVE_HEADERS
        )
    )


# 固定 reason code（Driver/Service 层共用）
REASON_OK = None
REASON_CONNECT_TIMEOUT = "wire_connect_timeout"
REASON_READ_TIMEOUT = "wire_read_timeout"
REASON_TOTAL_TIMEOUT = "wire_total_timeout"
REASON_TOO_MANY_REQUESTS = "wire_http_429"
REASON_RATE_LIMITED = "wire_rate_limited"
REASON_HTTP_5XX = "wire_http_5xx"
REASON_HTTP_4XX = "wire_http_4xx"
REASON_HTTP_425 = "wire_http_425"
REASON_MALFORMED_JSON = "wire_malformed_json"
REASON_EMPTY_RESPONSE = "wire_empty_response"
REASON_OFFSET_FORBIDDEN = "wire_offset_forbidden"
REASON_RESPONSE_SCHEMA = "wire_response_schema"
REASON_WS_DISCONNECT = "wire_ws_disconnect"
REASON_WS_PONG_TIMEOUT = "wire_ws_pong_timeout"
REASON_WS_SUBSCRIBE = "wire_ws_subscribe"
REASON_WS_FRAME = "wire_ws_frame"
REASON_HTTP_BATCH_TOO_LARGE = "wire_http_batch_too_large"

_retryable_5xx = re.compile(r"^5\d\d$")
_retryable_status = frozenset({429, 425})


def is_retryable_status(status: int) -> bool:
    """429/425/5xx 可重试；其余 4xx 不重试（任务 §5.1）。"""
    return status in _retryable_status or bool(_retryable_5xx.match(str(status)))
