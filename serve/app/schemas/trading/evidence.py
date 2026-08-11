"""Evidence typed DTO（WP-02 Checkpoint A）。

- ``PriorInput``：显式市场盲先验；禁止 quote/odds/crowd 字段。
- ``EvidenceRevisionInput``：四时态 + source/kind/branch + raw artifact + 污染状态。
- ``EvidenceCoveragePolicyInput``：cohort 冻结 coverage policy；widening 算法由 policy 声明。
- ``EvidenceBundleInput``：cutoff 后冻结的 as-of bundle。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# prior 顶层禁止字段（市场盲；任务 §2.2/架构 §2.3）
PRIOR_FORBIDDEN_KEYS = frozenset(
    {
        "probability", "odds", "quote", "price", "market_price", "edge", "belief",
        "market", "crowd", "label", "future_fact", "Q", "U",
    }
)

# evidence revision 合法 kind（架构 §3.1）
EVIDENCE_KINDS = frozenset(
    {"observation", "source_claim", "inference", "conflict", "missing"}
)
# 污染状态
TAINT_STATUSES = frozenset(
    {"none", "market", "odds", "crowd", "label", "future_fact", "quarantined"}
)


def _find_forbidden(value: Any, forbidden: frozenset[str], path: str = "input") -> str | None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if str(key).casefold() in forbidden:
                return child_path
            hit = _find_forbidden(child, forbidden, child_path)
            if hit is not None:
                return hit
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            hit = _find_forbidden(child, forbidden, f"{path}[{index}]")
            if hit is not None:
                return hit
    return None


class PriorInput(BaseModel):
    """显式 market-blind prior（架构 §2.3 / G4）。"""

    model_config = ConfigDict(extra="forbid")

    reference_class: str | None = Field(default=None, min_length=1)
    hazard_ref: str | None = Field(default=None, min_length=1)
    applicability: dict = Field(default_factory=dict)
    sample_rule: dict = Field(default_factory=dict)
    width: dict = Field(default_factory=dict)
    failure_conditions: dict = Field(default_factory=dict)
    market_blind_declaration: bool = True
    content: dict = Field(default_factory=dict)

    @field_validator("reference_class", "hazard_ref")
    @classmethod
    def _v_ref(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("prior_reference_blank")
        return v

    @field_validator("applicability", "sample_rule", "width", "failure_conditions", "content")
    @classmethod
    def _v_object(cls, v: dict) -> dict:
        if not isinstance(v, dict):
            raise ValueError("prior_field_not_object")
        forbidden = _find_forbidden(v, PRIOR_FORBIDDEN_KEYS)
        if forbidden is not None:
            raise ValueError(f"prior_forbidden_key:{forbidden}")
        return v

    @field_validator("market_blind_declaration")
    @classmethod
    def _v_blind(cls, v: bool) -> bool:
        if v is not True:
            raise ValueError("prior_market_blind_must_be_true")
        return v

    def require_reference(self) -> None:
        if not self.reference_class and not self.hazard_ref:
            raise ValueError("prior_reference_required")

    def require_structured(self) -> None:
        if not self.applicability or not self.sample_rule or not self.width or not self.failure_conditions:
            raise ValueError("prior_structure_incomplete")


class EvidenceRevisionInput(BaseModel):
    """evidence revision 候选（架构 §3.2）。"""

    model_config = ConfigDict(extra="forbid")

    revision_key: str = Field(min_length=1)
    kind: str = Field(pattern="^(observation|source_claim|inference|conflict|missing)$")
    event_at: datetime
    published_at: datetime
    observed_at: datetime
    ingested_at: datetime
    source: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    branch: str = Field(min_length=1)
    prev_revision_key: str | None = None
    raw_artifact_ref: str = Field(pattern=r"^[0-9a-f]{64}$")
    content: dict = Field(default_factory=dict)
    taint_status: str = Field(default="none", pattern="^(none|market|odds|crowd|label|future_fact|quarantined)$")
    market_conditioned_discovery: bool = False

    @field_validator("content")
    @classmethod
    def _v_content(cls, v: dict) -> dict:
        if not isinstance(v, dict):
            raise ValueError("evidence_content_not_object")
        return v

    def assert_time_order(self) -> None:
        if self.published_at > self.observed_at:
            raise ValueError("evidence_publish_after_observe")
        if self.observed_at > self.ingested_at:
            raise ValueError("evidence_observe_after_ingest")


class EvidenceCoveragePolicyInput(BaseModel):
    """冻结 coverage policy（架构 §3.3 / G5B）。"""

    model_config = ConfigDict(extra="forbid")

    policy_version: int = Field(gt=0)
    material_branches: list[str] = Field(min_length=1)
    allowed_source_types: list[str] = Field(min_length=1)
    contamination_policy: dict = Field(default_factory=dict)
    staleness_policy: dict = Field(default_factory=dict)
    independence_requirement: dict = Field(default_factory=dict)
    widening_algorithm: str = Field(min_length=1)
    missing_branch_policy: str = Field(
        pattern="^(widen|abstain)$", default="abstain"
    )
    content: dict = Field(default_factory=dict)

    @field_validator("material_branches")
    @classmethod
    def _v_branches(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("coverage_branches_duplicate")
        return v


class EvidenceBundleInput(BaseModel):
    """as-of bundle（架构 §3.3）。"""

    model_config = ConfigDict(extra="forbid")

    bundle_key: str = Field(min_length=1)
    information_cutoff_at: datetime
    # 有序 revision key 列表（输入乱序不改变 bundle hash —— Logic 排序后哈希）
    revision_keys: list[str] = Field(min_length=1)
