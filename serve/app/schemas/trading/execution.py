"""Shadow execution typed DTO（WP-03 Checkpoint C）。

只表达 typed 输入；不持有状态、不做业务判断。
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ShadowFillInput(BaseModel):
    """按 exact quote checkpoint depth 的 shadow fill 请求。"""

    model_config = ConfigDict(extra="forbid")

    execution_key: str = Field(min_length=1)
    economic_action_intent_id: int = Field(gt=0)
    action_set_leg_id: int = Field(gt=0)
    contract_spec_id: int = Field(gt=0)
    token_id: int = Field(gt=0)
    fill_role: str = Field(pattern="^(open|close|reduce)$")
    quantity: Decimal = Field(gt=0)
    side: str = Field(pattern="^(buy|sell)$")
    depth_levels: list[list[Decimal | int]] = Field(min_length=1)
    taker_fee_bps: Decimal = Field(default=Decimal("0"), ge=0)
    portfolio_namespace: str = Field(min_length=1)


class PositionUpdateInput(BaseModel):
    """position 重建（乐观锁）所需参数。"""

    model_config = ConfigDict(extra="forbid")

    portfolio_namespace: str = Field(min_length=1)
    contract_spec_id: int = Field(gt=0)
    token_id: int = Field(gt=0)
    market_id: int | None = None
    component_id: int | None = None
