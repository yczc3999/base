"""Workflow/Screening typed DTO（WP-01C Checkpoint B/C）。

- ``G0ObjectiveInput``：objective validator 的必需字段集（任务 §6.1）。
- ``R0Input``：R0 严格 allowlist DTO（任务 §6.2）；不接受 prior/schema/probability/edge。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# G0 objective 必需字段（任务 §6.1）
G0_REQUIRED_FIELDS = frozenset(
    {
        "objective_fn_version",
        "units",
        "decision_horizon",
        "HOLD_TO_RESOLUTION",
        "discount_policy",
        "capital_charge_policy",
        "NO_ACTION",
        "allowed_actions",
        "trading_cost_scope",
        "data_cost_scope",
        "llm_cost_scope",
        "search_cost_scope",
        "infrastructure_cost_scope",
        "human_cost_scope",
        "operational_cost_scope",
        "robustness_policy",
        "hard_constraint_ordering",
    }
)

# R0 输入 allowlist（任务 §6.2）
R0_ALLOWED_KEYS = frozenset(
    {
        "market_metadata",
        "end_at",
        "resolution_at",
        "rule_completeness",
        "best_bid",
        "best_ask",
        "depth",
        "fee_rate",
        "minimum_deployable_capacity",
        "speed_window",
        "estimated_research_cost",
        "estimated_research_latency",
        "objective_ref",
        "resource_envelope",
    }
)

R0_RESULTS = ("SELECT", "DEFER", "REJECT")

_OBJECT_POLICY_FIELDS = (
    "discount_policy",
    "capital_charge_policy",
    "robustness_policy",
)
_OBJECT_SCOPE_FIELDS = (
    "trading_cost_scope",
    "data_cost_scope",
    "llm_cost_scope",
    "search_cost_scope",
    "infrastructure_cost_scope",
    "human_cost_scope",
    "operational_cost_scope",
)


def _is_nonempty_structure(value: Any) -> bool:
    """Return true only for an explicit, non-empty object/list (never a scalar)."""

    return isinstance(value, (dict, list)) and not isinstance(value, bool) and bool(value)


class G0ObjectiveInput(BaseModel):
    """objective 内容快照；字段缺失由 ``missing_fields`` 暴露（任务 §6.1）。"""

    model_config = ConfigDict(extra="ignore")

    content: dict = Field(default_factory=dict)

    @field_validator("content")
    @classmethod
    def _v_content(cls, v: dict) -> dict:
        if not isinstance(v, dict):
            raise ValueError("objective_content_not_object")

        # Missing fields are retained as a typed G0 failure (``missing_fields``),
        # while fields that are present must already satisfy the frozen contract.
        for field in ("objective_fn_version", "units", "decision_horizon"):
            if field in v and (not isinstance(v[field], str) or not v[field].strip()):
                raise ValueError(f"objective_invalid_nonempty_string:{field}")
        if "HOLD_TO_RESOLUTION" in v and v["HOLD_TO_RESOLUTION"] is not True:
            raise ValueError("objective_hold_to_resolution_must_be_true")
        if "NO_ACTION" in v and not _is_nonempty_structure(v["NO_ACTION"]):
            raise ValueError("objective_no_action_not_structure")
        if "allowed_actions" in v:
            actions = v["allowed_actions"]
            if (
                not isinstance(actions, list)
                or not actions
                or any(not isinstance(action, str) or not action.strip() for action in actions)
                or "NO_ACTION" not in actions
            ):
                raise ValueError("objective_allowed_actions_invalid")
        for field in (*_OBJECT_POLICY_FIELDS, *_OBJECT_SCOPE_FIELDS):
            if field in v and not _is_nonempty_structure(v[field]):
                raise ValueError(f"objective_invalid_structure:{field}")
        if "hard_constraint_ordering" in v:
            ordering = v["hard_constraint_ordering"]
            if (
                not isinstance(ordering, list)
                or not ordering
                or any(not isinstance(item, str) or not item.strip() for item in ordering)
            ):
                raise ValueError("objective_hard_constraint_ordering_invalid")
        return v

    @property
    def missing_fields(self) -> list[str]:
        return sorted(G0_REQUIRED_FIELDS - set(self.content.keys()))


class HydratedFrameMarketInput(BaseModel):
    """Artifact Store 在事务外水化出的单个 frame member。"""

    model_config = ConfigDict(extra="forbid")

    market_id: int = Field(gt=0)
    metadata: dict = Field(default_factory=dict)


class HydratedUniverseFrameInput(BaseModel):
    """确切 COMPLETE REST frame；Screening 不从 mutable current 反推成员。"""

    model_config = ConfigDict(extra="forbid")

    frame_id: int = Field(gt=0)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_object_id: int = Field(gt=0)
    artifact_ref: str = Field(pattern=r"^[0-9a-f]{64}$")
    markets: list[HydratedFrameMarketInput]

    @model_validator(mode="after")
    def _unique_markets(self) -> "HydratedUniverseFrameInput":
        ids = [market.market_id for market in self.markets]
        if len(ids) != len(set(ids)):
            raise ValueError("hydrated_frame_duplicate_market")
        return self


class R0PolicyInput(BaseModel):
    """冻结 R0 判定阈值；禁止 Logic 内硬编码可变产品门槛。"""

    model_config = ConfigDict(extra="forbid")

    policy_version: int = Field(gt=0)
    minimum_rule_completeness: Decimal = Field(ge=0, le=1)
    maximum_research_cost: Decimal = Field(ge=0)
    require_two_sided_quote: bool = True
    defer_recheck_condition: str = Field(min_length=1)
    reject_recheck_condition: str = Field(min_length=1)


class RejectAuditPolicyInput(BaseModel):
    """冻结 reject/defer audit 设计；抽样率按结构化 stratum 读取。"""

    model_config = ConfigDict(extra="forbid")

    policy_version: int = Field(gt=0)
    algorithm_version: str = Field(min_length=1)
    salt: str = Field(min_length=1)
    reject_probability: Decimal = Field(ge=0, le=1)
    defer_probability: Decimal = Field(ge=0, le=1)


class R0Input(BaseModel):
    """R0 严格 allowlist 输入：未知 key 直接拒绝（任务 §6.2）。"""

    model_config = ConfigDict(extra="forbid")

    market_metadata: dict = Field(default_factory=dict)
    end_at: datetime | None = None
    resolution_at: datetime | None = None
    rule_completeness: Decimal | None = Field(default=None, ge=0, le=1)
    best_bid: Decimal | None = Field(default=None, gt=0, le=1)
    best_ask: Decimal | None = Field(default=None, gt=0, le=1)
    depth: dict = Field(default_factory=dict)
    fee_rate: Decimal | None = Field(default=None, ge=0)
    minimum_deployable_capacity: Decimal | None = Field(default=None, ge=0)
    speed_window: str | None = None
    estimated_research_cost: Decimal | None = Field(default=None, ge=0)
    estimated_research_latency: str | None = None
    objective_ref: str | None = None
    resource_envelope: dict = Field(default_factory=dict)

    def allowlist_ok(self) -> bool:
        """验证代码字段集与冻结 allowlist 全等，防止重命名后检查静默失真。"""
        return set(type(self).model_fields) == R0_ALLOWED_KEYS


class R0BatchItemInput(BaseModel):
    """One independently keyed R0 evaluation in a set-based frame batch."""

    model_config = ConfigDict(extra="forbid")

    market_id: int = Field(gt=0)
    episode_no: int = Field(gt=0)
    r0_input: R0Input
