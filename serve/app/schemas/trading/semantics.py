"""Contract/Component typed DTO（WP-01C Checkpoint A）。

Logic 接收 typed candidate，确定性校验并持久化（任务 §2.2）；schema 只解析/规范化，
不做业务判断。world schema 输入含认知/盘口字段拒绝逻辑（allowlist，任务 §5.1）。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.trading.payout import FORBIDDEN_STATES, validate_payout_ir

# world schema 顶层禁止字段（任务 §5.1）
SCHEMA_FORBIDDEN_KEYS = frozenset(
    {
        "probability", "odds", "quote", "price", "edge", "belief",
        "Q", "U", "mu", "expected_value", "payout", "market_price",
    }
)

# resolution states 合法集合（架构 §1.0）
KNOWN_RESOLUTION_STATES = frozenset(
    {"YES", "NO", "VOID", "PARTIAL", "OTHER", "INVALID", "REFUND"}
)


class PayoutIRInput(BaseModel):
    """单 token 的 payout truth table（canonical lookup，任务 §2.3）。

    ``pm_token_id`` 是内部 ``pm_tokens.id``，``token_version_id`` 是 exact
    ``pm_token_versions.id``（任务 §5.1 要求 payout 用内部 id + exact token-version）。
    """

    model_config = ConfigDict(extra="forbid")

    token_key: str = Field(min_length=1)
    pm_token_id: int = Field(gt=0)
    token_version_id: int = Field(gt=0)
    outcome_index: int = Field(ge=0)
    function_ir: dict[str, str]  # {resolution_state: decimal-string}
    test_vectors: dict[str, Any] = Field(default_factory=dict)

    @field_validator("function_ir")
    @classmethod
    def _v_ir(cls, v: dict[str, str]) -> dict[str, str]:
        # 只做结构校验；基数/完整性由 Logic 结合 spec 的 K_c/R_c 判定
        for state, val in v.items():
            if state in FORBIDDEN_STATES:
                raise ValueError(f"payout_unknown_terminal:{state}")
            if state not in KNOWN_RESOLUTION_STATES:
                raise ValueError(f"payout_unknown_state:{state}")
            validate_payout_ir(v, resolution_states=list(v.keys()))
        return v


class ContractSpecInput(BaseModel):
    """G1 typed candidate（任务 §2.2/§5.1）。"""

    model_config = ConfigDict(extra="forbid")

    contract_key: str = Field(min_length=1)
    market_version_id: int = Field(gt=0)
    yes_token_version_id: int = Field(gt=0)
    no_token_version_id: int = Field(gt=0)
    artifact_object_id: int = Field(gt=0)
    resolution_states: list[str] = Field(min_length=1)
    compiler_version: str = Field(min_length=1)
    schema_version: int = Field(gt=0)
    payouts: list[PayoutIRInput] = Field(min_length=1)
    # snapshot provenance（存 contract_snapshot）
    question: str | None = None
    rules: str | None = None
    clarification: str | None = None
    resolution_source: str | None = None
    # 简单、完整且无争议的规则不需要人为制造 clarification。只有上游 typed
    # compiler 明确识别到关键澄清依赖时，才把该位冻结为 True 并要求 clarification。
    clarification_required: bool = False

    @field_validator("resolution_states")
    @classmethod
    def _v_states(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("resolution_states_empty")
        for s in v:
            if s in FORBIDDEN_STATES:
                raise ValueError(f"resolution_unknown_terminal:{s}")
            if s not in KNOWN_RESOLUTION_STATES:
                raise ValueError(f"resolution_unknown_state:{s}")
        if len(set(v)) != len(v):
            raise ValueError("resolution_states_duplicate")
        return v

    @field_validator("payouts")
    @classmethod
    def _v_payouts(cls, v: list[PayoutIRInput]) -> list[PayoutIRInput]:
        keys = [p.token_key for p in v]
        if len(set(keys)) != len(keys):
            raise ValueError("payout_token_duplicate")
        return v


class WorldStateAssignmentInput(BaseModel):
    """一个有限 world 的显式、可重算赋值，而不是含义不明的字符串标签。"""

    model_config = ConfigDict(extra="forbid")

    world_state_id: str = Field(min_length=1)
    assignment: dict[str, Any]


class WorldSchemaInput(BaseModel):
    """G2 typed candidate：``Ω_d`` 有限变量/domain/constraint/factorization + ``h_c``（任务 §5.1）。"""

    model_config = ConfigDict(extra="forbid")

    component_key: str = Field(min_length=1)
    variables: dict[str, Any]
    domains: dict[str, Any]
    constraints: list[Any] = Field(default_factory=list)
    factorization: dict[str, Any]
    world_states: list[WorldStateAssignmentInput] = Field(min_length=1)
    state_count: int = Field(gt=0)
    # {contract_spec_id(str): {world_state_id: resolution_state}}
    h_c: dict[str, dict[str, str]]
    schema_version: int = Field(gt=0)

    @field_validator("variables")
    @classmethod
    def _v_variables(cls, v: dict[str, Any]) -> dict[str, Any]:
        forbidden = _find_forbidden_schema_key(v)
        if forbidden:
            raise ValueError(f"schema_forbidden_variable:{forbidden}")
        return v

    @field_validator("domains")
    @classmethod
    def _v_domains(cls, v: dict[str, Any]) -> dict[str, Any]:
        forbidden = _find_forbidden_schema_key(v)
        if forbidden:
            raise ValueError(f"schema_forbidden_domain:{forbidden}")
        return v

    @field_validator("constraints", "factorization")
    @classmethod
    def _v_recursive_schema_content(cls, v: Any) -> Any:
        forbidden = _find_forbidden_schema_key(v)
        if forbidden:
            raise ValueError(f"schema_forbidden_content:{forbidden}")
        return v

    @field_validator("h_c")
    @classmethod
    def _v_hc(cls, v: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
        for spec_id, mapping in v.items():
            for ws, state in mapping.items():
                if state in FORBIDDEN_STATES:
                    raise ValueError(f"hc_unknown_terminal:{state}")
                if state not in KNOWN_RESOLUTION_STATES:
                    raise ValueError(f"hc_unknown_state:{state}")
        return v

    @field_validator("world_states")
    @classmethod
    def _v_world_states(
        cls, v: list[WorldStateAssignmentInput]
    ) -> list[WorldStateAssignmentInput]:
        ids = [state.world_state_id for state in v]
        if len(set(ids)) != len(ids):
            raise ValueError("world_states_duplicate")
        return v


def _find_forbidden_schema_key(value: Any, path: str = "schema") -> str | None:
    """递归拒绝认知、盘口与经济判断字段，大小写不敏感。"""

    forbidden = {key.casefold() for key in SCHEMA_FORBIDDEN_KEYS}
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if str(key).casefold() in forbidden:
                return child_path
            hit = _find_forbidden_schema_key(child, child_path)
            if hit is not None:
                return hit
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            hit = _find_forbidden_schema_key(child, f"{path}[{index}]")
            if hit is not None:
                return hit
    return None
