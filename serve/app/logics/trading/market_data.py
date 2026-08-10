"""Market data Logic（WP-01B Checkpoint D）。

- ``freshness`` 是无 DB/网络纯判断：epoch 非 LIVE、snapshot 不完整、book crossed、
  bid/ask 缺失、年龄超 TTL 任一返回固定 hard-stop reason（任务 §5.4）。
- ``BookState`` + ``apply_delta`` 是纯内存订单簿：full book 原子替换、``price_change``
  按 price 更新（``size=0`` 删除）、best bid/ask 从有序结构求值（绝不取数组 ``[0]``）。
- 不制造 0.5/空簿：无有效 book 时返回 hard-stop。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

# hard-stop reason codes（固定）
REASON_EPOCH_NOT_LIVE = "quote_epoch_not_live"
REASON_SNAPSHOT_INCOMPLETE = "quote_snapshot_incomplete"
REASON_BOOK_CROSSED = "quote_book_crossed"
REASON_SIDE_MISSING = "quote_side_missing"
REASON_TOO_OLD = "quote_too_old"
REASON_STALE = "quote_stale"
REASON_TICK_MISMATCH = "quote_tick_mismatch"

# 价格约定（quote binding 用）
PRICE_CONVENTION_USD_CENTS = "usd-cents"


@dataclass(frozen=True)
class FreshnessPolicy:
    """quote freshness 策略（显式 policy fixture，任务 §2.9）。"""

    quote_ttl_s: float = 30.0
    max_depth_levels: int = 50
    price_convention: str = PRICE_CONVENTION_USD_CENTS


@dataclass(frozen=True)
class FreshnessDecision:
    """freshness 判定；``live=False`` 时 reason 为固定 hard-stop code。"""

    live: bool
    reason: str | None = None
    age_s: float | None = None

    @property
    def hard_stop(self) -> str | None:
        return None if self.live else self.reason


def freshness(
    policy: FreshnessPolicy,
    now: datetime,
    *,
    epoch_status: str,
    checkpoint: dict[str, Any] | None,
    best_bid: Decimal | None,
    best_ask: Decimal | None,
) -> FreshnessDecision:
    """纯判断：任何不满足项 → 固定 hard-stop reason；绝不制造可交易报价。"""
    if epoch_status != "LIVE":
        return FreshnessDecision(False, REASON_EPOCH_NOT_LIVE)
    if checkpoint is None:
        return FreshnessDecision(False, REASON_SNAPSHOT_INCOMPLETE)
    if checkpoint.get("validity") == "STALE":
        return FreshnessDecision(False, REASON_STALE)
    if checkpoint.get("validity") == "CROSSED":
        return FreshnessDecision(False, REASON_BOOK_CROSSED)
    if not checkpoint.get("completeness"):
        return FreshnessDecision(False, REASON_SNAPSHOT_INCOMPLETE)
    if best_bid is None or best_ask is None:
        return FreshnessDecision(False, REASON_SIDE_MISSING)
    if best_bid >= best_ask:
        return FreshnessDecision(False, REASON_BOOK_CROSSED)
    received = checkpoint.get("received_at")
    if not isinstance(received, datetime):
        return FreshnessDecision(False, REASON_SNAPSHOT_INCOMPLETE)
    age_s = (now - received).total_seconds()
    if age_s < 0 or age_s > policy.quote_ttl_s:
        return FreshnessDecision(False, REASON_TOO_OLD, age_s=age_s)
    return FreshnessDecision(True, None, age_s=age_s)


def _fmt(decimal_value: Decimal | None) -> str | None:
    return str(decimal_value) if decimal_value is not None else None


def _canonical_dec(value: Decimal) -> str:
    """Decimal 规范化文本：去掉数值尾零/指数，跨存储 scale 稳定。"""
    return format(value.normalize(), "f")


@dataclass(frozen=True)
class BookState:
    """进程内热 book（L0）；不可变替换。bids/asks 为 price→size 有序映射。"""

    token_id: str
    bids: dict[Decimal, Decimal] = field(default_factory=dict)
    asks: dict[Decimal, Decimal] = field(default_factory=dict)
    tick_size: Decimal | None = None
    min_order_size: Decimal | None = None
    epoch_id: int | None = None
    validity: str = "SYNCING"
    observed_at: datetime | None = None

    @property
    def best_bid(self) -> Decimal | None:
        return max(self.bids, default=None)

    @property
    def best_ask(self) -> Decimal | None:
        return min(self.asks, default=None)

    @property
    def crossed(self) -> bool:
        bb, ba = self.best_bid, self.best_ask
        return bb is not None and ba is not None and bb >= ba

    def depth_hash(self) -> str:
        """canonical depth hash：side 有序 + price 升序。

        Decimal 用 ``normalize()+f`` 规范化（NUMERIC(38,12) 读回带尾零，直接 str() 会
        破坏跨存储 scale 的 hash 稳定性；与 outbox canonical 序列化同源要求）。
        """
        payload = {
            "token_id": self.token_id,
            "bids": sorted((_canonical_dec(p), _canonical_dec(s)) for p, s in self.bids.items()),
            "asks": sorted((_canonical_dec(p), _canonical_dec(s)) for p, s in self.asks.items()),
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


def snapshot_book(
    *,
    token_id: str,
    bids: list[tuple[Decimal, Decimal]],
    asks: list[tuple[Decimal, Decimal]],
    tick_size: Decimal | None,
    min_order_size: Decimal | None,
    epoch_id: int | None,
    observed_at: datetime,
    validity: str = "VALID",
) -> BookState:
    """full snapshot 原子替换（任务 §5.2：initial dump / REST baseline 到齐才 cutover）。"""
    return BookState(
        token_id=token_id,
        bids={price: size for price, size in bids},
        asks={price: size for price, size in asks},
        tick_size=tick_size,
        min_order_size=min_order_size,
        epoch_id=epoch_id,
        validity=validity,
        observed_at=observed_at,
    )


def apply_delta(
    state: BookState,
    *,
    changes: list[tuple[str, Decimal, Decimal]],
    epoch_id: int,
    received_at: datetime,
    validity: str = "VALID",
) -> BookState:
    """应用 price_change delta：size=0 删除该档，否则按 price 覆盖。跨 epoch 拒绝。"""
    if state.epoch_id is not None and epoch_id != state.epoch_id:
        raise ValueError("delta_epoch_mismatch")
    bids = dict(state.bids)
    asks = dict(state.asks)
    for side, price, size in changes:
        target = bids if side == "bid" else asks
        if size == 0:
            target.pop(price, None)
        else:
            target[price] = size
    return replace(
        state,
        bids=bids,
        asks=asks,
        epoch_id=epoch_id,
        validity=validity,
        observed_at=received_at,
    )


def price_change_freshness(
    policy: FreshnessPolicy,
    now: datetime,
    state: BookState,
) -> FreshnessDecision:
    """当前热 book 的 freshness（epoch 已 LIVE、无 crossed/缺边/过期）。"""
    if state.validity != "VALID":
        return FreshnessDecision(False, REASON_STALE)
    bb, ba = state.best_bid, state.best_ask
    if bb is None or ba is None:
        return FreshnessDecision(False, REASON_SIDE_MISSING)
    if bb >= ba:
        return FreshnessDecision(False, REASON_BOOK_CROSSED)
    if state.observed_at is None:
        return FreshnessDecision(False, REASON_SNAPSHOT_INCOMPLETE)
    age_s = (now - state.observed_at).total_seconds()
    if age_s < 0 or age_s > policy.quote_ttl_s:
        return FreshnessDecision(False, REASON_TOO_OLD, age_s=age_s)
    return FreshnessDecision(True, None, age_s=age_s)
