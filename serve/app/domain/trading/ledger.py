"""双分录账本 domain 助手（WP-03 Checkpoint C）。

- ``build_buy_postings``：BUY 至少形成 portfolio↔shadow venue 的 cash 与 token 两组对手
  posting（≥4 postings），每种 asset 分别归零。
- ``postings_balanced``：每个 (asset_type, asset_key) signed 合计必须为 0 且 ≥2 postings。
- ``build_reversal``：对一组 posting 生成精确相反 reversal（纠错只用 reversal）。
- 全部 base-unit 整数（Decimal scale 0）。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.domain.trading.rounding import round_cash, round_quantity

ZERO = Decimal("0")

ASSET_CASH = "CASH"
ASSET_TOKEN = "TOKEN"


def _dec(value: Decimal | str | int) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("ledger_bool_forbidden")
    if isinstance(value, float):
        raise ValueError("ledger_float_forbidden")
    return Decimal(str(value))


@dataclass(frozen=True)
class Posting:
    """一条 ledger posting（base-unit signed 金额）。"""

    asset_type: str
    asset_key: str
    amount: Decimal  # signed base-unit
    counterparty: str

    def __post_init__(self) -> None:
        if self.asset_type not in (ASSET_CASH, ASSET_TOKEN):
            raise ValueError(f"ledger_asset_type_unknown:{self.asset_type}")
        if not self.asset_key:
            raise ValueError("ledger_asset_key_empty")


def build_buy_postings(
    *,
    venue: str,
    portfolio_namespace: str,
    cash_asset_key: str,
    token_asset_key: str,
    cash_spent: Decimal | str,
    token_quantity: Decimal | str,
) -> list[Posting]:
    """BUY：portfolio 付出现金、收进 token；venue 收进现金、付出 token。

    - CASH: portfolio −cash_spent / venue +cash_spent → 归零
    - TOKEN: portfolio +token_quantity / venue −token_quantity → 归零
    至少 4 postings，每组 asset 两条对手。
    """
    cash = round_cash(_dec(cash_spent))
    tokens = round_quantity(_dec(token_quantity))
    if cash < 0 or tokens < 0:
        raise ValueError("ledger_buy_negative")
    return [
        Posting(ASSET_CASH, cash_asset_key, -cash, portfolio_namespace),
        Posting(ASSET_CASH, cash_asset_key, cash, venue),
        Posting(ASSET_TOKEN, token_asset_key, tokens, portfolio_namespace),
        Posting(ASSET_TOKEN, token_asset_key, -tokens, venue),
    ]


def postings_balanced(postings: list[Posting]) -> bool:
    """每个 (asset_type, asset_key) 的 signed 合计=0 且 ≥2 postings。"""
    sums: dict[tuple[str, str], Decimal] = {}
    counts: dict[tuple[str, str], int] = {}
    for posting in postings:
        key = (posting.asset_type, posting.asset_key)
        sums[key] = sums.get(key, ZERO) + posting.amount
        counts[key] = counts.get(key, 0) + 1
    return all(value == ZERO and counts[k] >= 2 for k, value in sums.items())


def imbalance(postings: list[Posting]) -> dict[tuple[str, str], Decimal]:
    """返回每个 asset 的 signed 合计（非零表示不平衡）。"""
    sums: dict[tuple[str, str], Decimal] = {}
    for posting in postings:
        key = (posting.asset_type, posting.asset_key)
        sums[key] = sums.get(key, ZERO) + posting.amount
    return sums


def build_reversal(postings: list[Posting]) -> list[Posting]:
    """对给定 posting 集生成精确相反 reversal（amount 取反）。"""
    return [
        Posting(p.asset_type, p.asset_key, -p.amount, p.counterparty)
        for p in postings
    ]


def net_cash_flow(postings: list[Posting], *, cash_asset_key: str) -> Decimal:
    """某 cash 资产的净流量（portfolio 视角：付出现金为负）。"""
    return round_cash(
        sum((p.amount for p in postings if p.asset_type == ASSET_CASH and p.asset_key == cash_asset_key),
            ZERO)
    )
