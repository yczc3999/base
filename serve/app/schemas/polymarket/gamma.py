"""Gamma API wire schema（WP-01B Checkpoint A）。

只解析/规范化，不发网络（实施合同 §5.1）。

- keyset 分页页结构：``events``/``markets``/``data`` 三种键都接受并归一为 ``items``；
  ``next_cursor`` 为空串视为终止。
- ``outcomes``/``clobTokenIds``/``outcomePrices`` 是 JSON 字符串数组或真实数组 → 二次解析
  并按同一 index 绑定；已知类型错误 fail-closed，未知字段进 ``raw_extra``。
- ``BinaryMarketAssessment`` 是纯解析态（COMPLETE/INCOMPLETE/CONFLICT），供 universe Logic
  决定 eligible 标记；不在此做业务判断。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Annotated, Any, ClassVar

from pydantic import AliasChoices, BeforeValidator, Field, field_validator

from app.schemas.polymarket.common import (
    DecimalPrice,
    DecimalNonNegative,
    DecimalSigned,
    PolymarketModel,
    ProviderDateTime,
    parse_decimal_array,
    parse_json_string_array,
)

# Gamma keyset 官方 limit（任务 §2.3）：events ≤500、markets ≤100。
GAMMA_EVENTS_PAGE_LIMIT = 500
GAMMA_MARKETS_PAGE_LIMIT = 100


def _string_list(value: Any) -> list[str]:
    parsed = parse_json_string_array(value, "string_array")
    if not all(isinstance(item, str) for item in parsed):
        raise ValueError("string_array_not_all_strings")
    return parsed


def _decimal_list(value: Any) -> list[Decimal]:
    return parse_decimal_array(value, "decimal_array")


# ---------------- Event / Market ----------------

class GammaEvent(PolymarketModel):
    """Gamma event（catalog 权威，events/keyset 与 detail 通用）。"""

    id: Annotated[str, Field(validation_alias="id")]
    slug: str | None = None
    title: str | None = None
    description: str | None = None
    start_date: ProviderDateTime | None = Field(default=None, validation_alias="startDate")
    end_date: ProviderDateTime | None = Field(default=None, validation_alias="endDate")
    active: bool | None = None
    closed: bool | None = None
    archived: bool | None = None
    tags: list[str] = Field(default_factory=list)
    volume: DecimalNonNegative | None = None
    liquidity: DecimalNonNegative | None = None
    # event 内嵌 market 数组（detail 响应常见）；keyset events 页为扁平数组
    markets: list["GammaMarket"] = Field(default_factory=list)


class GammaMarket(PolymarketModel):
    """Gamma market。ID 分列保存，禁止混用（任务 §2.2）。"""

    id: Annotated[str, Field(validation_alias="id")]
    question: str | None = None
    question_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("questionID", "questionId"),
    )
    condition_id: str | None = Field(default=None, validation_alias="conditionId")
    slug: str | None = None
    ticker: str | None = None
    img_url: str | None = Field(default=None, validation_alias="imgUrl")

    # JSON 字符串数组 → 解析为列表（保持原值在 raw_extra/artifact）
    outcomes: list[str] = Field(default_factory=list)
    clob_token_ids: list[str] = Field(default_factory=list, validation_alias="clobTokenIds")
    outcome_prices: list[Decimal] = Field(default_factory=list, validation_alias="outcomePrices")

    active: bool | None = None
    closed: bool | None = None
    archived: bool | None = None
    accepting_orders: bool | None = Field(default=None, validation_alias="acceptingOrders")
    enable_order_book: bool | None = Field(default=None, validation_alias="enableOrderBook")
    neg_risk: bool | None = Field(default=None, validation_alias="negRisk")
    neg_risk_market_id: str | None = Field(default=None, validation_alias="negRiskMarketID")

    start_date: ProviderDateTime | None = Field(default=None, validation_alias="startDate")
    start_date_iso: ProviderDateTime | None = Field(default=None, validation_alias="startDateIso")
    end_date: ProviderDateTime | None = Field(default=None, validation_alias="endDate")
    end_date_iso: ProviderDateTime | None = Field(default=None, validation_alias="endDateIso")
    closed_time: ProviderDateTime | None = Field(default=None, validation_alias="closedTime")
    game_start_time: ProviderDateTime | None = Field(default=None, validation_alias="gameStartTime")

    volume: DecimalNonNegative | None = None
    liquidity: DecimalNonNegative | None = None
    volume_num: DecimalNonNegative | None = Field(default=None, validation_alias="volumeNum")
    liquidity_num: DecimalNonNegative | None = Field(default=None, validation_alias="liquidityNum")
    spread: DecimalPrice | None = None
    best_bid: DecimalPrice | None = Field(default=None, validation_alias="bestBid")
    best_ask: DecimalPrice | None = Field(default=None, validation_alias="bestAsk")
    last_trade_price: DecimalPrice | None = Field(default=None, validation_alias="lastTradePrice")
    one_day_price_change: DecimalSigned | None = Field(
        default=None, validation_alias="oneDayPriceChange"
    )

    @field_validator("outcomes", mode="before")
    @classmethod
    def _v_outcomes(cls, v: Any) -> list[str]:
        return _string_list(v)

    @field_validator("clob_token_ids", mode="before")
    @classmethod
    def _v_clob_tokens(cls, v: Any) -> list[str]:
        return _string_list(v)

    @field_validator("outcome_prices", mode="before")
    @classmethod
    def _v_outcome_prices(cls, v: Any) -> list[Decimal]:
        prices = _decimal_list(v)
        if any(price < 0 or price > 1 for price in prices):
            raise ValueError("outcome_price_out_of_range")
        return prices


GammaEvent.model_rebuild()


# ---------------- keyset 分页 ----------------

class GammaEventsKeysetPage(PolymarketModel):
    """Gamma events keyset 页（Driver 归一后构造）。"""

    items: list[GammaEvent] = Field(default_factory=list)
    next_cursor: str | None = None


class GammaMarketsKeysetPage(PolymarketModel):
    """Gamma markets keyset 页（Driver 归一后构造）。"""

    items: list[GammaMarket] = Field(default_factory=list)
    next_cursor: str | None = None


def parse_gamma_keyset_page(
    raw: dict[str, Any], *, items_key: str
) -> tuple[list[dict[str, Any]], str | None]:
    """从原始响应提取 ``<items_key>`` 数组与 ``next_cursor``（纯函数，供 Driver 使用）。

    官方两种返回形态都接受：``{<items_key>: [...], next_cursor}`` 与 ``{data: [...], next_cursor}``。
    ``next_cursor`` 空串视为终止（返回 None）。offset 参数由 Driver 层拒绝。
    """
    if not isinstance(raw, dict):
        raise ValueError("keyset_response_not_object")
    items = raw.get(items_key)
    if items is None and "data" in raw:
        items = raw.get("data")
    if items is None:
        raise ValueError(f"keyset_missing_{items_key}")
    if not isinstance(items, list):
        raise ValueError(f"keyset_{items_key}_not_array")
    if "next_cursor" not in raw or raw["next_cursor"] is None:
        return items, None
    cursor = raw["next_cursor"]
    if not isinstance(cursor, str):
        raise ValueError("keyset_next_cursor_invalid_type")
    return items, cursor or None


# ---------------- 二元市场解析态（纯判定）----------------

@dataclass(frozen=True)
class BinaryMarketAssessment:
    """Gamma market 的 YES(0)/NO(1) 二元映射解析态；只做解析，不做业务判断。"""

    complete: bool
    outcome_count: int
    token_count: int
    price_count: int
    reason: str | None  # None=COMPLETE；其余固定 code


REASON_MAPPING_COMPLETE = None
REASON_MAPPING_INCOMPLETE = "mapping_incomplete"
REASON_MAPPING_LENGTH_MISMATCH = "mapping_length_mismatch"
REASON_MAPPING_LABEL_CONFLICT = "mapping_label_conflict"
REASON_MAPPING_TOKEN_CONFLICT = "mapping_token_conflict"
REASON_MAPPING_INDEX_OUT_OF_RANGE = "mapping_index_out_of_range"


def assess_binary_market(
    outcomes: list[str] | None,
    clob_token_ids: list[str] | None,
    outcome_prices: list[Decimal] | None,
    *,
    neg_risk: bool | None = None,
) -> BinaryMarketAssessment:
    """判定 Gamma market 是否构成完整二元 YES/NO 映射（任务 §2.2 / §5.2）。

    - 恰好 2 个 outcome、2 个 token；index 0/1 分别应为 YES/NO（大小写不敏感）；长度互相一致。
    - 任一数组缺失/空 → INCOMPLETE（保留事实，不进入 eligible）。
    - 长度不一致 / label 非 YES·NO / 价格数组缺失 → 相应固定 reason。
    """
    outs = outcomes or []
    tokens = clob_token_ids or []
    prices = outcome_prices or []

    # 任何数组超过二元都是确定的 provider 冲突；即使另一数组缺失，也不能
    # 降格成普通 incomplete 而掩盖这个已知矛盾。
    if len(outs) > 2 or len(tokens) > 2 or len(prices) > 2:
        return BinaryMarketAssessment(False, len(outs), len(tokens), len(prices), REASON_MAPPING_LENGTH_MISMATCH)
    # 部分映射（不足二元）→ INCOMPLETE：保留事实，不进入 eligible。
    if len(outs) < 2 or len(tokens) < 2 or not prices:
        return BinaryMarketAssessment(False, len(outs), len(tokens), len(prices), REASON_MAPPING_INCOMPLETE)
    # 其余非 2/2/2 长度组合为冲突。
    if len(outs) != 2 or len(tokens) != 2 or len(prices) != 2:
        return BinaryMarketAssessment(False, len(outs), len(tokens), len(prices), REASON_MAPPING_LENGTH_MISMATCH)

    lower = [label.strip().lower() for label in outs]
    if lower[0] != "yes" or lower[1] != "no":
        return BinaryMarketAssessment(False, len(outs), len(tokens), len(prices), REASON_MAPPING_LABEL_CONFLICT)
    if any(not isinstance(token, str) or not token.strip() for token in tokens) or len(set(tokens)) != 2:
        return BinaryMarketAssessment(False, len(outs), len(tokens), len(prices), REASON_MAPPING_TOKEN_CONFLICT)

    return BinaryMarketAssessment(True, len(outs), len(tokens), len(prices), REASON_MAPPING_COMPLETE)
