"""官方精度规则（WP-03 Checkpoint B）。

金额/概率用 Decimal，禁止 float；share 数量与 cash 用 base-unit 整数（NUMERIC(38,0)）。
确定性：同一输入永远同一输出（无 banker's rounding 歧义，全部用 ROUND_HALF_UP + 固定小数位）。
"""

from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

PRICE_PLACES = 6          # 价格小数位（p_execution_spec_v1.rounding.price_decimal_places）
CASH_PLACES = 0           # cash base-unit（scale 0）
SHARE_PLACES = 0          # share quantity base-unit（scale 0）


def _dec(value: Decimal | str | int) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("rounding_bool_forbidden")
    if isinstance(value, float):
        raise ValueError("rounding_float_forbidden")
    return Decimal(str(value))


def round_price(value: Decimal | str | int) -> Decimal:
    """价格保留 PRICE_PLACES 位（ROUND_HALF_UP）。"""
    dec = _dec(value)
    if not dec.is_finite():
        raise ValueError("rounding_price_not_finite")
    return dec.quantize(Decimal(1).scaleb(-PRICE_PLACES), rounding=ROUND_HALF_UP)


def round_cash(value: Decimal | str | int) -> Decimal:
    """cash 保留 CASH_PLACES 位整数（base units）。"""
    dec = _dec(value)
    if not dec.is_finite():
        raise ValueError("rounding_cash_not_finite")
    return dec.quantize(Decimal(1).scaleb(-CASH_PLACES), rounding=ROUND_HALF_UP)


def round_quantity(value: Decimal | str | int) -> Decimal:
    """share 数量保留 SHARE_PLACES 位整数（base units）。"""
    dec = _dec(value)
    if not dec.is_finite():
        raise ValueError("rounding_quantity_not_finite")
    return dec.quantize(Decimal(1).scaleb(-SHARE_PLACES), rounding=ROUND_HALF_UP)


def floor_quantity(value: Decimal | str | int) -> Decimal:
    """买入可成交数量向下取整（conservative，不造 book 中不存在的数量）。"""
    dec = _dec(value)
    if not dec.is_finite():
        raise ValueError("rounding_quantity_not_finite")
    return dec.quantize(Decimal(1).scaleb(-SHARE_PLACES), rounding=ROUND_DOWN)


def shares_to_cash(quantity: Decimal, price: Decimal) -> Decimal:
    """quantity × price，round to cash base units（ROUND_HALF_UP）。"""
    return round_cash(_dec(quantity) * _dec(price))


def cash_to_share_value(cash: Decimal, price: Decimal) -> Decimal:
    """cash ÷ price → share quantity（floor，conservative）。"""
    price_dec = _dec(price)
    if price_dec <= 0:
        raise ValueError("rounding_price_nonpositive")
    return floor_quantity(_dec(cash) / price_dec)


def tick_floor(price: Decimal, tick_size: Decimal) -> Decimal:
    """按 tick_size 向下对齐（价格不得低于 tick 网格）。"""
    tick = _dec(tick_size)
    if tick <= 0:
        raise ValueError("rounding_tick_nonpositive")
    price_dec = _dec(price)
    return (price_dec / tick).quantize(Decimal(1), rounding=ROUND_DOWN) * tick


def tick_ceil(price: Decimal, tick_size: Decimal) -> Decimal:
    """按 tick_size 向上对齐。"""
    tick = _dec(tick_size)
    if tick <= 0:
        raise ValueError("rounding_tick_nonpositive")
    price_dec = _dec(price)
    return (price_dec / tick).quantize(Decimal(1), rounding=ROUND_HALF_UP) * tick
