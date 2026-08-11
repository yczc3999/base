"""Decision / G7A / G7B typed DTO（WP-03 Checkpoint B）。

Logic 接收 typed candidate；schema 只解析/规范化，不做业务判断。strict allowlist
（``extra="forbid"``）防任意字段注入。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

DECISION_MODES = ("BLIND_ONLY", "LINEAR_SHRINKAGE")
ACTION_TYPES = (
    "BUY_TOKEN", "ADD_TOKEN", "SELL_TOKEN_TO_REDUCE", "SELL_TOKEN_TO_CLOSE",
    "HOLD", "FLIP",
)
DISPOSITIONS = ("ACTION", "WAIT", "ABSTAIN")
LEG_ROLES = ("open", "close", "reduce", "hold")


class QuoteRevealInput(BaseModel):
    """揭价输入：decision 所需 exact token set 的 quote 绑定。"""

    model_config = ConfigDict(extra="forbid")

    trade_decision_key: str = Field(min_length=1)
    episode_id: int = Field(gt=0)
    forecast_submission_id: int = Field(gt=0)
    # token_id -> checkpoint 引用
    token_quotes: dict[int, dict[str, Any]] = Field(default_factory=dict)


class MarketRelativeInput(BaseModel):
    """market-relative decision 构造输入。"""

    model_config = ConfigDict(extra="forbid")

    decision_mode: str = Field(default="BLIND_ONLY", pattern="^(BLIND_ONLY|LINEAR_SHRINKAGE)$")
    w_blind: Decimal | None = Field(default=None, ge=0, le=1)
    q_blind: dict[str, str] = Field(min_length=1)
    # {token_id: best_ask} 用于 shrinkage 的 coherent Q_market 构造（完整互斥集时）
    token_prices: dict[int, str] = Field(default_factory=dict)

    @field_validator("q_blind")
    @classmethod
    def _v_q(cls, v: dict[str, str]) -> dict[str, str]:
        for key, value in v.items():
            if not isinstance(key, str) or not key:
                raise ValueError("decision_q_state_key_invalid")
            try:
                dec = Decimal(value)
            except Exception as exc:
                raise ValueError(f"decision_q_invalid_decimal:{key}") from exc
            if not dec.is_finite() or dec < 0:
                raise ValueError(f"decision_q_invalid_value:{key}")
        return v


class ActionCandidateInput(BaseModel):
    """G7A typed candidate：token/action 的可执行估值输入。"""

    model_config = ConfigDict(extra="forbid")

    contract_spec_id: int = Field(gt=0)
    token_id: int = Field(gt=0)
    action_type: str = Field(pattern="^(BUY_TOKEN|ADD_TOKEN|SELL_TOKEN_TO_REDUCE|SELL_TOKEN_TO_CLOSE|HOLD|FLIP)$")
    target_quantity: Decimal = Field(gt=0)
    # {price: size} 深度级别（buy=ask，sell=bid）
    depth_levels: list[list[Decimal | int]] = Field(min_length=1)
    side: str = Field(pattern="^(buy|sell)$")
    taker_fee_bps: Decimal = Field(default=Decimal("0"), ge=0)
    horizon_days: Decimal = Field(default=Decimal("1"), gt=0)
    bankroll: Decimal = Field(gt=0)

    @field_validator("depth_levels")
    @classmethod
    def _v_depth(cls, v: list[list[Decimal | int]]) -> list[list[Decimal | int]]:
        for level in v:
            if len(level) != 2:
                raise ValueError("decision_depth_level_shape")
        return v


class PortfolioGateInput(BaseModel):
    """G7B typed candidate：组合保险丝 + 边际效用。"""

    model_config = ConfigDict(extra="forbid")

    bankroll: Decimal = Field(gt=0)
    per_market_cap: Decimal = Field(default=Decimal("0.04"), ge=0, le=1)
    per_component_cap: Decimal = Field(default=Decimal("0.06"), ge=0, le=1)
    global_cap: Decimal = Field(default=Decimal("0.30"), ge=0, le=1)
    market_exposure: Decimal = Field(ge=0)
    component_exposure: Decimal = Field(ge=0)
    global_exposure: Decimal = Field(ge=0)


class ActionSetInput(BaseModel):
    """终态 action set（terminal decision 绑定）。"""

    model_config = ConfigDict(extra="forbid")

    disposition: str = Field(pattern="^(ACTION|WAIT|ABSTAIN)$")
    reason_code: str | None = None
    wake_condition: str | None = None
    recheck_at: datetime | None = None
    # {leg_role: {contract_spec_id: {token_id: quantity}}}
    legs: dict[str, dict[int, dict[int, Decimal]]] = Field(default_factory=dict)


class UnderwritingInput(BaseModel):
    """预承保计划（架构 §6.3）。"""

    model_config = ConfigDict(extra="forbid")

    plan_version: int = Field(gt=0)
    entry_range: dict = Field(default_factory=dict)
    hold_to_resolution: bool = True
    thesis_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    invalidation: dict = Field(default_factory=dict)
    wake_condition: str | None = None
    edge_close_threshold: Decimal | None = Field(default=None, ge=0, le=1)
    time_stop_at: datetime | None = None
