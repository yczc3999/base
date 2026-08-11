"""Settlement / label typed DTO（WP-04 Checkpoint B）。

只表达 typed 输入；strict allowlist（``extra="forbid"``）防任意字段注入。
Logic 决定状态机与证据核验；本包只做严格解析/规范化。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

LABEL_STATES = (
    "pending", "provisional", "disputed", "final_admissible", "final_excluded"
)
CLUSTER_SPLITS = ("train", "validation", "forward_holdout")
CLUSTER_STATUS = ("OPEN", "FROZEN", "RESOLVED")
TARGET_TYPES = ("bernoulli", "multiclass", "mean_only")


class LabelRevisionInput(BaseModel):
    """一条 label revision 的 typed 输入（identity 由 contract_spec+label_key 派生）。"""

    model_config = ConfigDict(extra="forbid")

    contract_spec_id: int = Field(gt=0)
    label_key: str = Field(min_length=1)
    state: str = Field(
        pattern="^(pending|provisional|disputed|final_admissible|final_excluded)$"
    )
    resolution_state: str | None = None
    resolution_source: str | None = None
    evidence_artifact_id: int | None = Field(default=None, gt=0)
    raw_outcome: dict | None = None
    token_cashflow: dict | None = None
    policy_code_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    auditor_identity: str | None = None
    supersedes_id: int | None = Field(default=None, gt=0)
    exclusion_reason: str | None = None
    conflict_set: list | None = None


class ClusterInput(BaseModel):
    """resolution cluster 创建输入（创建时 outcome 未知）。"""

    model_config = ConfigDict(extra="forbid")

    cluster_key: str = Field(min_length=1)
    cluster_version: int = Field(gt=0)
    split: str = Field(pattern="^(train|validation|forward_holdout)$")
    time_block_start: datetime
    time_block_end: datetime
    horizon: str = Field(min_length=1)
    status: str = Field(default="OPEN", pattern="^(OPEN|FROZEN|RESOLVED)$")


class ScoreTargetInput(BaseModel):
    """canonical score target 创建输入（type/shape 互斥）。"""

    model_config = ConfigDict(extra="forbid")

    target_key: str = Field(min_length=1)
    target_type: str = Field(pattern="^(bernoulli|multiclass|mean_only)$")
    contract_spec_id: int = Field(gt=0)
    payout_function_id: int | None = Field(default=None, gt=0)
    canonical_side: str | None = Field(default=None, pattern="^(YES|NO)$")
    members: list[str] | None = None
    payout_type: str | None = None
