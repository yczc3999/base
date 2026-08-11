"""Forecast typed DTO（WP-02 Checkpoint A）。

- ``ForecastSubmissionInput``：``Q``（world-state→decimal-string 联合分布）与 ``U``
  （非空、去重、含 Q 的 coherent distribution 集）；schema 只做结构与进制校验，
  coherence/normalize 由 domain.probability 确定性计算（任务 §2.4）。
- ``PayoutProjectionInput``：单个 spec×token 的提交候选；最终 μ/V/bounds 由
  domain.probability push-forward 生成，不接受 LLM 直接输出（任务 §2.4/§4.3）。
- ``ForecastLeaseInput``：valid_until + 结构化 invalidation conditions（架构 §4.4）。
- ``CoherenceCheckInput``：G6 确定性 check 的提交结果。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# submission/Q/U 顶层禁止字段（blind 物理边界；任务 §2.1）
BLIND_FORBIDDEN_KEYS = frozenset(
    {
        "quote", "odds", "price", "bid", "ask", "depth", "market", "crowd",
        "label", "edge", "expected_value_market", "p_decision",
    }
)


def _find_forbidden(value: Any, path: str = "input") -> str | None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if str(key).casefold() in BLIND_FORBIDDEN_KEYS:
                return child_path
            hit = _find_forbidden(child, child_path)
            if hit is not None:
                return hit
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            hit = _find_forbidden(child, f"{path}[{index}]")
            if hit is not None:
                return hit
    return None


class QDistributionInput(BaseModel):
    """``Q: Ω_d → [0,1]`` 联合概率分布（world-state-id → decimal-string）。"""

    model_config = ConfigDict(extra="forbid")

    values: dict[str, str] = Field(min_length=1)

    @field_validator("values")
    @classmethod
    def _v_values(cls, v: dict[str, str]) -> dict[str, str]:
        if not isinstance(v, dict):
            raise ValueError("q_values_not_object")
        for key, value in v.items():
            if not isinstance(key, str) or not key:
                raise ValueError("q_state_key_invalid")
            if not isinstance(value, str):
                raise ValueError("q_value_not_string")
            try:
                dec = Decimal(value)
            except Exception as exc:
                raise ValueError(f"q_invalid_decimal:{key}") from exc
            if not dec.is_finite():
                raise ValueError(f"q_not_finite:{key}")
            if dec < 0:
                raise ValueError(f"q_negative:{key}")
        return v


class ForecastSubmissionInput(BaseModel):
    """G6 typed candidate：``Q`` 与 ``U``（含 Q）。"""

    model_config = ConfigDict(extra="forbid")

    submission_key: str = Field(min_length=1)
    Q: QDistributionInput
    U: list[QDistributionInput] = Field(min_length=1)
    forecast_input_manifest_id: int = Field(gt=0)

    @field_validator("Q")
    @classmethod
    def _v_q(cls, v: QDistributionInput) -> QDistributionInput:
        forbidden = _find_forbidden(v.values, "Q")
        if forbidden is not None:
            raise ValueError(f"blind_forbidden_key:{forbidden}")
        return v

    @field_validator("U")
    @classmethod
    def _v_u(cls, v: list[QDistributionInput]) -> list[QDistributionInput]:
        for index, member in enumerate(v):
            forbidden = _find_forbidden(member.values, f"U[{index}]")
            if forbidden is not None:
                raise ValueError(f"blind_forbidden_key:{forbidden}")
        # 去重由 domain.probability.validate_u 以 canonical hash 判定（值相同即重复）
        return v


class PayoutProjectionInput(BaseModel):
    """单 spec×token 的确定性投影候选（μ/V/bounds 由 Logic 重算覆盖，不接受 LLM 值）。"""

    model_config = ConfigDict(extra="forbid")

    contract_spec_id: int = Field(gt=0)
    pm_token_id: int = Field(gt=0)
    algorithm_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class ForecastLeaseInput(BaseModel):
    """forecast lease：valid_until + 机器可判断 invalidation（架构 §4.4）。"""

    model_config = ConfigDict(extra="forbid")

    valid_until: datetime
    invalidation_conditions: dict = Field(default_factory=dict)
    evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    schema_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    spec_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("invalidation_conditions")
    @classmethod
    def _v_conditions(cls, v: dict) -> dict:
        if not isinstance(v, dict):
            raise ValueError("lease_conditions_not_object")
        forbidden = _find_forbidden(v, "lease_conditions")
        if forbidden is not None:
            raise ValueError(f"blind_forbidden_key:{forbidden}")
        return v


class CoherenceCheckInput(BaseModel):
    """G6 确定性 check 结果；hard check 失败由 Logic 阻塞 commit。"""

    model_config = ConfigDict(extra="forbid")

    check_name: str = Field(min_length=1)
    passed: bool
    severity: str = Field(pattern="^(hard|soft)$")
    reason_code: str | None = None
    details_artifact_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
