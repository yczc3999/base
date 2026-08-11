"""Shadow execution typed DTO（WP-03 Checkpoint C）。

只表达 typed 输入；不持有状态、不做业务判断。
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ShadowFillInput(BaseModel):
    """请求执行一个已经冻结的 action-set leg。

    除 ``execution_key`` / intent / leg identity 外的字段仅为旧事件兼容提示；执行层不把
    caller 提供的 token、数量、方向、depth、fee 或 namespace 当作事实。它们全部从
    intent → action set → decision → quote binding / frozen execution spec 读取。
    """

    model_config = ConfigDict(extra="forbid")

    execution_key: str = Field(min_length=1)
    economic_action_intent_id: int = Field(gt=0)
    action_set_leg_id: int = Field(gt=0)
    contract_spec_id: int | None = Field(default=None, gt=0)
    token_id: int | None = Field(default=None, gt=0)
    fill_role: str | None = Field(default=None, pattern="^(open|close|reduce)$")
    quantity: Decimal | None = Field(default=None, gt=0)
    side: str | None = Field(default=None, pattern="^(buy|sell)$")
    depth_levels: list[list[Decimal | int]] | None = None
    taker_fee_bps: Decimal | None = Field(default=None, ge=0)
    portfolio_namespace: str | None = Field(default=None, min_length=1)


class PositionUpdateInput(BaseModel):
    """position 重建（乐观锁）所需参数。"""

    model_config = ConfigDict(extra="forbid")

    portfolio_namespace: str = Field(min_length=1)
    contract_spec_id: int = Field(gt=0)
    token_id: int = Field(gt=0)
    market_id: int | None = None
    component_id: int | None = None
