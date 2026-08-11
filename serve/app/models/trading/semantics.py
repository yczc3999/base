"""Trading semantics models（WP-01C Checkpoint A，revision ``b1000012``）。

8 张表：contract snapshot/spec、payout function、forecast component/version、
world schema、component membership、portfolio dependency edge。

不变量（DB 强制，任务 §5.2 / 架构 §4.1）：
- snapshot 只作 G1 provenance（引用 pm_market_versions + 两条 token_versions + artifact）。
- contract_spec 保存 canonical ``K_c/R_c``、token/state count、compiler/schema version、status、
  content hash、G1 reason；PASS spec 恰引用一个 snapshot。
- payout_functions 用内部 ``pm_token_id`` + exact token-version id；``function_ir`` 是
  ``{resolution_state: decimal-string}`` 全量 lookup；``contract_spec×token`` 唯一。
- world_schema 只保存 finite variables/domains/constraint/factorization/``h_c``；禁止认知/盘口字段
  （DB JSONB 不校验字段，由 G2 Logic 的 allowlist 校验；DB 保证 h_c 是 object）。
- component version 非空、恰引一个同 component 的 world schema；membership 唯一。
- portfolio_dependency_edges 两端不同、canonical 排序去重；只作组合 stress。
- 全部 append-only（immutable trigger 复用 0002 的 ``v2_reject_immutable_row``）。
- deferred trigger ``v2_check_payout_completeness``：payout 数量=|K_c|、token 属于 snapshot market、
  key 集=R_c、值 0..1（提交时核验）。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import ForeignKey, Index

from app.models.trading.constants import TRADING_SCHEMA
from app.models.trading.mixins import (
    BigIntIdentityMixin,
    CreatedAtMixin,
    TradingBase,
)
from app.models.trading.types import (
    base_unit_type,
    external_id_type,
    sha256_type,
    utc_timestamp_type,
)

SPEC_STATUS = ("pending", "pass", "fail", "superseded")
COMPONENT_STATUS = ("draft", "active", "retired")


class ContractSnapshot(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """contract 原始输入 provenance；只作 G1 输入，不被下游当 spec FK（任务 §5.1）。"""

    __tablename__ = "contract_snapshots"
    __table_args__ = (
        UniqueConstraint("content_hash", name="uq_contract_snapshots_content_hash"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_contract_snapshots_hash_hex"),
        CheckConstraint(
            "yes_token_version_id <> no_token_version_id",
            name="ck_contract_snapshots_distinct_token_versions",
        ),
        Index("ix_contract_snapshots_market_version", "market_version_id"),
        {"schema": TRADING_SCHEMA},
    )

    market_version_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.pm_market_versions.id", name="fk_contract_snapshots_market_version"),
        nullable=False,
    )
    yes_token_version_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.pm_token_versions.id", name="fk_contract_snapshots_yes_token_version"),
        nullable=False,
    )
    no_token_version_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.pm_token_versions.id", name="fk_contract_snapshots_no_token_version"),
        nullable=False,
    )
    artifact_object_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.artifact_objects.id", name="fk_contract_snapshots_artifact"),
        nullable=False,
    )
    question: Mapped[str | None] = mapped_column(Text)
    rules: Mapped[str | None] = mapped_column(Text)
    clarification: Mapped[str | None] = mapped_column(Text)
    resolution_source: Mapped[str | None] = mapped_column(Text)
    cutoff_at: Mapped[datetime | None] = mapped_column(utc_timestamp_type())
    timezone_name: Mapped[str | None] = mapped_column(String(64))
    raw_outcome_mapping: Mapped[dict | None] = mapped_column(JSONB)
    content_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)


class ContractSpec(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """G1 后唯一 contract 语义身份：canonical ``K_c/R_c`` + status（任务 §2.1）。"""

    __tablename__ = "contract_specs"
    __table_args__ = (
        UniqueConstraint("contract_key", "version_no", name="uq_contract_specs_key_version"),
        UniqueConstraint("contract_key", "content_hash", name="uq_contract_specs_key_hash"),
        CheckConstraint(
            "status IN ('pending','pass','fail','superseded')",
            name="ck_contract_specs_status_known",
        ),
        CheckConstraint("token_count > 0", name="ck_contract_specs_token_count_positive"),
        CheckConstraint("state_count > 0", name="ck_contract_specs_state_count_positive"),
        CheckConstraint(
            "jsonb_typeof(kc_resolution_states) = 'array'",
            name="ck_contract_specs_states_array",
        ),
        CheckConstraint(
            "jsonb_typeof(token_ids) = 'object'",
            name="ck_contract_specs_token_ids_object",
        ),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_contract_specs_hash_hex"),
        Index("ix_contract_specs_snapshot", "snapshot_id"),
        {"schema": TRADING_SCHEMA},
    )

    contract_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.contract_snapshots.id", name="fk_contract_specs_snapshot"),
        nullable=False,
    )
    kc_resolution_states: Mapped[dict] = mapped_column(JSONB, nullable=False)  # ["YES","NO",...]
    token_ids: Mapped[dict] = mapped_column(JSONB, nullable=False)  # {0: pm_token_id, 1: ...}
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    state_count: Mapped[int] = mapped_column(Integer, nullable=False)
    compiler_version: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="pending")
    content_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    g1_reason: Mapped[str | None] = mapped_column(String(128))


class PayoutFunction(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """``g_{c,t}:R_c→Decimal``：canonical lookup truth table（任务 §2.3/§5.1）。"""

    __tablename__ = "payout_functions"
    __table_args__ = (
        UniqueConstraint("contract_spec_id", "pm_token_id", name="uq_payout_functions_spec_token"),
        UniqueConstraint(
            "contract_spec_id", "outcome_index",
            name="uq_payout_functions_spec_outcome",
        ),
        CheckConstraint(
            "jsonb_typeof(function_ir) = 'object'",
            name="ck_payout_functions_ir_object",
        ),
        CheckConstraint(
            "jsonb_typeof(test_vectors) = 'object'",
            name="ck_payout_functions_vectors_object",
        ),
        CheckConstraint("outcome_index >= 0", name="ck_payout_functions_outcome_nonneg"),
        CheckConstraint("algorithm_hash ~ '^[0-9a-f]{64}$'", name="ck_payout_functions_alg_hash_hex"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_payout_functions_content_hash_hex"),
        Index("ix_payout_functions_spec", "contract_spec_id"),
        {"schema": TRADING_SCHEMA},
    )

    contract_spec_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.contract_specs.id", name="fk_payout_functions_spec"),
        nullable=False,
    )
    pm_token_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.pm_tokens.id", name="fk_payout_functions_token"),
        nullable=False,
    )
    token_version_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.pm_token_versions.id", name="fk_payout_functions_token_version"),
        nullable=False,
    )
    outcome_index: Mapped[int] = mapped_column(Integer, nullable=False)
    function_ir: Mapped[dict] = mapped_column(JSONB, nullable=False)
    test_vectors: Mapped[dict] = mapped_column(JSONB, nullable=False)
    algorithm_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    content_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)


class ForecastComponent(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """联合预测边界（master；只保存边界/成本预算，不保存 schema 双写）。"""

    __tablename__ = "forecast_components"
    __table_args__ = (
        UniqueConstraint("component_key", name="uq_forecast_components_key"),
        CheckConstraint("cost_budget >= 0", name="ck_forecast_components_cost_budget_nonneg"),
        {"schema": TRADING_SCHEMA},
    )

    component_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    cost_budget: Mapped[Decimal | None] = mapped_column(base_unit_type())
    description: Mapped[str | None] = mapped_column(Text)


class WorldSchemaVersion(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """``Ω_d``：有限变量/domain/constraint/factorization + ``h_c`` lookup（任务 §5.1）。"""

    __tablename__ = "world_schema_versions"
    __table_args__ = (
        UniqueConstraint("component_id", "version_no", name="uq_world_schema_versions_component_version"),
        UniqueConstraint("component_id", "content_hash", name="uq_world_schema_versions_component_hash"),
        CheckConstraint(
            "jsonb_typeof(variables) = 'object'",
            name="ck_world_schema_versions_variables_object",
        ),
        CheckConstraint(
            "jsonb_typeof(domains) = 'object'",
            name="ck_world_schema_versions_domains_object",
        ),
        CheckConstraint(
            "jsonb_typeof(constraints) = 'array'",
            name="ck_world_schema_versions_constraints_array",
        ),
        CheckConstraint(
            "jsonb_typeof(factorization) = 'object'",
            name="ck_world_schema_versions_factorization_object",
        ),
        CheckConstraint(
            "jsonb_typeof(resolution_map) = 'object'",
            name="ck_world_schema_versions_resolution_map_object",
        ),
        CheckConstraint(
            "jsonb_typeof(h_c) = 'object'",
            name="ck_world_schema_versions_hc_object",
        ),
        CheckConstraint(
            "jsonb_typeof(world_states) = 'array'",
            name="ck_world_schema_versions_world_states_array",
        ),
        CheckConstraint(
            "state_count > 0 AND state_count <= 4096",
            name="ck_world_schema_versions_state_count_budget",
        ),
        CheckConstraint(
            "jsonb_array_length(world_states) = state_count",
            name="ck_world_schema_versions_state_count_exact",
        ),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_world_schema_versions_hash_hex"),
        CheckConstraint("status IN ('draft','active','retired')", name="ck_world_schema_versions_status_known"),
        {"schema": TRADING_SCHEMA},
    )

    component_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.forecast_components.id", name="fk_world_schema_versions_component"),
        nullable=False,
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    variables: Mapped[dict] = mapped_column(JSONB, nullable=False)
    domains: Mapped[dict] = mapped_column(JSONB, nullable=False)
    constraints: Mapped[list] = mapped_column(JSONB, nullable=False)
    factorization: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # [{world_state_id: str, assignment: object}]（每项 exact 两键；DB guard 校验）。
    world_states: Mapped[list] = mapped_column(JSONB, nullable=False)
    state_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # {contract_spec_content_hash: {world_state_id: resolution_state}}
    resolution_map: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # h_c: {contract_spec_id: {world_state_id: resolution_state}}（每 member 的 h_c）
    h_c: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="draft")
    content_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)


class ForecastComponentVersion(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """component version：边界/membership/生效区间；恰引一个 world schema（任务 §5.2）。"""

    __tablename__ = "forecast_component_versions"
    __table_args__ = (
        UniqueConstraint("component_id", "version_no", name="uq_forecast_component_versions_component_version"),
        UniqueConstraint(
            "component_id", "content_hash",
            name="uq_forecast_component_versions_component_hash",
        ),
        CheckConstraint(
            "status IN ('draft','active','retired')",
            name="ck_forecast_component_versions_status_known",
        ),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_forecast_component_versions_hash_hex"),
        {"schema": TRADING_SCHEMA},
    )

    component_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.forecast_components.id", name="fk_forecast_component_versions_component"),
        nullable=False,
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    world_schema_version_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.world_schema_versions.id", name="fk_forecast_component_versions_schema"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="draft")
    effective_from: Mapped[datetime | None] = mapped_column(utc_timestamp_type())
    effective_until: Mapped[datetime | None] = mapped_column(utc_timestamp_type())
    content_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    cost_budget: Mapped[Decimal | None] = mapped_column(base_unit_type())


class ForecastComponentContractSpec(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """``component_version × contract_spec_id`` 唯一 membership（任务 §5.2）。"""

    __tablename__ = "forecast_component_contract_specs"
    __table_args__ = (
        UniqueConstraint(
            "component_version_id", "contract_spec_id",
            name="uq_forecast_component_contract_specs_pair",
        ),
        CheckConstraint(
            "jsonb_typeof(h_c) = 'object'",
            name="ck_forecast_component_contract_specs_hc_object",
        ),
        CheckConstraint(
            "totality_test_hash ~ '^[0-9a-f]{64}$'",
            name="ck_forecast_component_contract_specs_totality_hash",
        ),
        Index("ix_forecast_component_contract_specs_cv", "component_version_id"),
        {"schema": TRADING_SCHEMA},
    )

    component_version_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.forecast_component_versions.id", name="fk_forecast_component_contract_specs_cv"),
        nullable=False,
    )
    contract_spec_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.contract_specs.id", name="fk_forecast_component_contract_specs_spec"),
        nullable=False,
    )
    # 该 member 在 component 内的 h_c：{world_state_id: resolution_state}
    h_c: Mapped[dict] = mapped_column(JSONB, nullable=False)
    totality_test_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)


class PortfolioDependencyEdge(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """跨 component 组合 stress 边；两端不同、canonical 排序去重（任务 §5.2/§2.6）。"""

    __tablename__ = "portfolio_dependency_edges"
    __table_args__ = (
        UniqueConstraint(
            "from_component_id", "to_component_id", "relation",
            name="uq_portfolio_dependency_edges_edge",
        ),
        CheckConstraint(
            "from_component_id < to_component_id",
            name="ck_portfolio_dependency_edges_canonical_order",
        ),
        CheckConstraint(
            "relation IN ('stress','correlation')",
            name="ck_portfolio_dependency_edges_relation_known",
        ),
        CheckConstraint(
            "edge_kind NOT IN ('probability_identity','coherence')",
            name="ck_portfolio_dependency_edges_no_prob_identity",
        ),
        {"schema": TRADING_SCHEMA},
    )

    from_component_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.forecast_components.id", name="fk_portfolio_dependency_edges_from"),
        nullable=False,
    )
    to_component_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("trading.forecast_components.id", name="fk_portfolio_dependency_edges_to"),
        nullable=False,
    )
    relation: Mapped[str] = mapped_column(String(32), nullable=False)
    edge_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    edge_metadata: Mapped[dict | None] = mapped_column(JSONB)
