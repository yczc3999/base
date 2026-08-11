"""Trading AI invocation models（WP-02 Checkpoint B，revision ``b1000021``）。

3 张 UTC 月 RANGE 分区事实表（无 default partition）：ai_invocations、ai_tool_calls、
ai_validation_results。分区键 ``occurred_at`` 必须包含在 PK 中（``PRIMARY KEY (id, occurred_at)``）；
跨分区幂等唯一走非分区 ``idempotency_claims``（任务 §4.1/实施合同 §4.1）。

不变量（任务 §4.1 / ai-observability-replay-design §2-§3）：
- 一次 provider request = 一次 attempt；retry/fallback/cache hit 各建新 invocation，
  绝不覆盖、拼接或静默换模型（``parent/retry_of/fallback_of`` 记录因果）。
- 生命周期：PLANNED→STARTED→TOOL_RUNNING*→RESPONSE_RECEIVED→PARSED→VALIDATED→
  ACCEPTED|REJECTED；异常终态 FAILED|TIMEOUT|CANCELLED|UNKNOWN。
- requested 与 returned provider/route/model 分列；returned 未 allowlist 直接 REJECTED。
- terminal row 禁止 update/delete（immutable guard 复用 ``v2_reject_immutable_row``）。
- Blind role 的 tool count=0；researcher/verifier 每个引用必须有 tool receipt。
- AI 请求/响应/parsed/normalized/tool 结果/validator 详情引用 Artifact Store，不进普通日志。
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKeyConstraint,
    Identity,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import Index

from app.models.trading.constants import TRADING_SCHEMA
from app.models.trading.mixins import CreatedAtMixin, TradingBase
from app.models.trading.types import (
    base_unit_type,
    external_id_type,
    sha256_type,
    utc_timestamp_type,
)

# 生命周期（任务 §4.1）
INVOCATION_LIFECYCLE = (
    "PLANNED", "STARTED", "TOOL_RUNNING", "RESPONSE_RECEIVED", "PARSED",
    "VALIDATED", "ACCEPTED", "REJECTED", "FAILED", "TIMEOUT", "CANCELLED", "UNKNOWN",
)
# 网络策略
NETWORK_POLICIES = ("NONE", "WEB_X", "SEARCH_URL")
# 上下文分类（blind 只允许前三类）
CONTEXT_CLASSES = (
    "CONTRACT", "PRIOR", "EVIDENCE", "QUOTE", "ODDS", "CROWD", "LABEL", "FUTURE_FACT",
)
# 工具调用状态
TOOL_STATUS = ("STARTED", "COMPLETED", "FAILED", "TIMED_OUT", "CANCELLED")
# validator severity
VALIDATOR_SEVERITY = ("hard", "soft")


class AIInvocation(TradingBase, CreatedAtMixin):
    """一次模型 attempt（UTC 月 RANGE 分区）。"""

    __tablename__ = "ai_invocations"
    __table_args__ = (
        PrimaryKeyConstraint("id", "occurred_at", name="pk_ai_invocations"),
        ForeignKeyConstraint(
            ["episode_id"],
            ["trading.forecast_episodes.id"],
            name="fk_ai_invocations_episode",
        ),
        ForeignKeyConstraint(
            ["model_role_binding_id"],
            ["trading.model_role_bindings.id"],
            name="fk_ai_invocations_model_role_binding",
        ),
        UniqueConstraint(
            "episode_id", "stage", "role", "experiment_variant", "attempt_no", "occurred_at",
            name="uq_ai_invocations_attempt_identity",
        ),
        CheckConstraint(
            "lifecycle_state IN ('PLANNED','STARTED','TOOL_RUNNING','RESPONSE_RECEIVED',"
            "'PARSED','VALIDATED','ACCEPTED','REJECTED','FAILED','TIMEOUT','CANCELLED','UNKNOWN')",
            name="ck_ai_invocations_lifecycle_known",
        ),
        CheckConstraint(
            "network_policy IN ('NONE','WEB_X','SEARCH_URL')",
            name="ck_ai_invocations_network_policy_known",
        ),
        CheckConstraint(
            "context_class IN ('CONTRACT','PRIOR','EVIDENCE','QUOTE','ODDS','CROWD',"
            "'LABEL','FUTURE_FACT')",
            name="ck_ai_invocations_context_class_known",
        ),
        CheckConstraint(
            "requested_provider IN ('deepseek','xai','gemini','kimi','packy')",
            name="ck_ai_invocations_requested_provider_known",
        ),
        CheckConstraint(
            "returned_provider IS NULL OR "
            "returned_provider IN ('deepseek','xai','gemini','kimi','packy')",
            name="ck_ai_invocations_returned_provider_known",
        ),
        CheckConstraint(
            "accepted_at IS NOT NULL = (lifecycle_state = 'ACCEPTED')",
            name="ck_ai_invocations_accepted_pair",
        ),
        CheckConstraint(
            "(lifecycle_state IN ('REJECTED','FAILED','TIMEOUT','CANCELLED','UNKNOWN')) = "
            "(terminal_reason IS NOT NULL)",
            name="ck_ai_invocations_terminal_pair",
        ),
        CheckConstraint(
            "attempt_no > 0",
            name="ck_ai_invocations_attempt_no_positive",
        ),
        CheckConstraint(
            "cost_estimated >= 0",
            name="ck_ai_invocations_cost_estimated_nonneg",
        ),
        CheckConstraint(
            "tool_count >= 0 AND search_count >= 0 AND search_count <= tool_count",
            name="ck_ai_invocations_tool_counts_valid",
        ),
        CheckConstraint(
            "tool_count = 0 OR network_policy <> 'NONE'",
            name="ck_ai_invocations_tool_requires_network",
        ),
        CheckConstraint(
            "context_class IN ('CONTRACT','PRIOR','EVIDENCE') OR network_policy <> 'NONE'",
            name="ck_ai_invocations_revealed_requires_network",
        ),
        CheckConstraint(
            "jsonb_typeof(input_manifest) = 'object'",
            name="ck_ai_invocations_input_manifest_object",
        ),
        CheckConstraint(
            "jsonb_typeof(pricing_snapshot) = 'object'",
            name="ck_ai_invocations_pricing_snapshot_object",
        ),
        CheckConstraint(
            "jsonb_typeof(taint_report) = 'object'",
            name="ck_ai_invocations_taint_report_object",
        ),
        CheckConstraint(
            "cache_key_hash IS NULL OR cache_key_hash ~ '^[0-9a-f]{64}$'",
            name="ck_ai_invocations_cache_key_hash_hex",
        ),
        CheckConstraint(
            "lifecycle_state <> 'ACCEPTED' OR ("
            "result IS NOT NULL AND result IN ('accepted','cache_hit') AND "
            "model_role_binding_id IS NOT NULL AND "
            "returned_provider = requested_provider AND "
            "returned_route = requested_route AND returned_model = requested_model AND "
            "prompt_version IS NOT NULL AND schema_version IS NOT NULL AND "
            "request_artifact_ref IS NOT NULL AND "
            "request_artifact_ref ~ '^[0-9a-f]{64}$' AND "
            "prompt_artifact_ref IS NOT NULL AND "
            "prompt_artifact_ref ~ '^[0-9a-f]{64}$' AND "
            "schema_artifact_ref IS NOT NULL AND "
            "schema_artifact_ref ~ '^[0-9a-f]{64}$' AND "
            "raw_response_artifact_ref IS NOT NULL AND "
            "raw_response_artifact_ref ~ '^[0-9a-f]{64}$' AND "
            "parsed_output_artifact_ref IS NOT NULL AND "
            "parsed_output_artifact_ref ~ '^[0-9a-f]{64}$' AND "
            "normalized_output_artifact_ref IS NOT NULL AND "
            "normalized_output_artifact_ref ~ '^[0-9a-f]{64}$' AND "
            "accepted_output_binding = normalized_output_artifact_ref AND "
            "(network_policy <> 'NONE' OR cache_key_hash IS NOT NULL) AND "
            "queued_at IS NOT NULL AND started_at IS NOT NULL AND "
            "response_at IS NOT NULL AND parsed_at IS NOT NULL AND "
            "validated_at IS NOT NULL AND completed_at IS NOT NULL"
            ")",
            name="ck_ai_invocations_accepted_shape",
        ),
        Index("ix_ai_invocations_episode", "episode_id", "occurred_at"),
        Index("ix_ai_invocations_role", "role", "occurred_at"),
        Index("ix_ai_invocations_cache_key", "cache_key_hash", "occurred_at"),
        {"schema": TRADING_SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False, primary_key=True)
    invocation_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    episode_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    experiment_variant: Mapped[str] = mapped_column(String(64), nullable=False, server_default="champion")
    # 因果
    parent_invocation_id: Mapped[int | None] = mapped_column(BigInteger)
    retry_of_invocation_id: Mapped[int | None] = mapped_column(BigInteger)
    fallback_of_invocation_id: Mapped[int | None] = mapped_column(BigInteger)
    causation_event_id: Mapped[str | None] = mapped_column(external_id_type())
    # 版本
    release_manifest_id: Mapped[int | None] = mapped_column(BigInteger)
    strategy_version_id: Mapped[int | None] = mapped_column(BigInteger)
    config_version_id: Mapped[int | None] = mapped_column(BigInteger)
    git_sha: Mapped[str | None] = mapped_column(sha256_type())
    db_revision: Mapped[str | None] = mapped_column(String(64))
    model_role_binding_id: Mapped[int | None] = mapped_column(BigInteger)
    # 模型（requested/returned 分列）
    requested_provider: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_route: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_model: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    returned_provider: Mapped[str | None] = mapped_column(String(32))
    returned_route: Mapped[str | None] = mapped_column(String(64))
    returned_model: Mapped[str | None] = mapped_column(external_id_type())
    effort: Mapped[str | None] = mapped_column(String(32))
    sampling: Mapped[dict | None] = mapped_column(JSONB)
    seed: Mapped[int | None] = mapped_column(BigInteger)
    cache_key_hash: Mapped[str | None] = mapped_column(sha256_type())
    # 权限/污染
    network_policy: Mapped[str] = mapped_column(String(32), nullable=False)
    allowed_tools: Mapped[list | None] = mapped_column(JSONB)
    allowed_domains: Mapped[list | None] = mapped_column(JSONB)
    context_class: Mapped[str] = mapped_column(String(32), nullable=False)
    taint_report: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # Prompt / 输入
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    prompt_artifact_ref: Mapped[str | None] = mapped_column(sha256_type())
    schema_version: Mapped[str | None] = mapped_column(String(64))
    schema_artifact_ref: Mapped[str | None] = mapped_column(sha256_type())
    input_manifest: Mapped[dict] = mapped_column(JSONB, nullable=False)
    input_manifest_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    # Request / 输出 Artifact
    request_artifact_ref: Mapped[str | None] = mapped_column(sha256_type())
    raw_response_artifact_ref: Mapped[str | None] = mapped_column(sha256_type())
    parsed_output_artifact_ref: Mapped[str | None] = mapped_column(sha256_type())
    normalized_output_artifact_ref: Mapped[str | None] = mapped_column(sha256_type())
    # 生命周期
    lifecycle_state: Mapped[str] = mapped_column(String(32), nullable=False, server_default="PLANNED")
    result: Mapped[str | None] = mapped_column(String(32))
    terminal_reason: Mapped[str | None] = mapped_column(String(128))
    retriable: Mapped[bool | None] = mapped_column(Boolean)
    accepted_at: Mapped[datetime | None] = mapped_column(utc_timestamp_type())
    # 时间
    queued_at: Mapped[datetime | None] = mapped_column(utc_timestamp_type())
    started_at: Mapped[datetime | None] = mapped_column(utc_timestamp_type())
    first_token_at: Mapped[datetime | None] = mapped_column(utc_timestamp_type())
    response_at: Mapped[datetime | None] = mapped_column(utc_timestamp_type())
    parsed_at: Mapped[datetime | None] = mapped_column(utc_timestamp_type())
    validated_at: Mapped[datetime | None] = mapped_column(utc_timestamp_type())
    completed_at: Mapped[datetime | None] = mapped_column(utc_timestamp_type())
    # 用量 / 成本
    input_tokens: Mapped[int | None] = mapped_column(BigInteger)
    cache_tokens: Mapped[int | None] = mapped_column(BigInteger)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger)
    reasoning_tokens: Mapped[int | None] = mapped_column(BigInteger)
    tool_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    search_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    provider_request_id: Mapped[str | None] = mapped_column(external_id_type())
    cost_estimated: Mapped[object] = mapped_column(base_unit_type(), nullable=False, server_default="0")
    cost_currency: Mapped[str | None] = mapped_column(String(16))
    pricing_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    cost_reconciliation: Mapped[str | None] = mapped_column(String(32))
    # 下游绑定
    accepted_output_binding: Mapped[str | None] = mapped_column(external_id_type())


class AIToolCall(TradingBase, CreatedAtMixin):
    """每次工具调用（UTC 月 RANGE 分区）；引用 AIInvocation。"""

    __tablename__ = "ai_tool_calls"
    __table_args__ = (
        PrimaryKeyConstraint("id", "occurred_at", name="pk_ai_tool_calls"),
        UniqueConstraint(
            "invocation_id", "invocation_occurred_at", "ordinal", "occurred_at",
            name="uq_ai_tool_calls_invocation_ordinal",
        ),
        ForeignKeyConstraint(
            ["invocation_id", "invocation_occurred_at"],
            ["trading.ai_invocations.id", "trading.ai_invocations.occurred_at"],
            name="fk_ai_tool_calls_invocation",
        ),
        CheckConstraint(
            "status IN ('STARTED','COMPLETED','FAILED','TIMED_OUT','CANCELLED')",
            name="ck_ai_tool_calls_status_known",
        ),
        CheckConstraint("ordinal >= 0", name="ck_ai_tool_calls_ordinal_nonneg"),
        CheckConstraint(
            "jsonb_typeof(arguments) = 'object'",
            name="ck_ai_tool_calls_arguments_object",
        ),
        CheckConstraint(
            "completed_at IS NOT NULL = (status = 'COMPLETED')",
            name="ck_ai_tool_calls_completed_pair",
        ),
        CheckConstraint(
            "cost >= 0",
            name="ck_ai_tool_calls_cost_nonneg",
        ),
        Index(
            "ix_ai_tool_calls_invocation",
            "invocation_id",
            "invocation_occurred_at",
            "occurred_at",
        ),
        {"schema": TRADING_SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False, primary_key=True)
    invocation_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    invocation_occurred_at: Mapped[datetime] = mapped_column(
        utc_timestamp_type(), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_type: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_version: Mapped[str | None] = mapped_column(String(64))
    arguments: Mapped[dict] = mapped_column(JSONB, nullable=False)
    arguments_artifact_ref: Mapped[str | None] = mapped_column(sha256_type())
    started_at: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(utc_timestamp_type())
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(128))
    result_artifact_ref: Mapped[str | None] = mapped_column(sha256_type())
    source_urls: Mapped[list | None] = mapped_column(JSONB)
    published_at: Mapped[datetime | None] = mapped_column(utc_timestamp_type())
    observed_at: Mapped[datetime | None] = mapped_column(utc_timestamp_type())
    usage: Mapped[dict | None] = mapped_column(JSONB)
    cost: Mapped[object] = mapped_column(base_unit_type(), nullable=False, server_default="0")
    provider_tool_call_id: Mapped[str | None] = mapped_column(external_id_type())


class AIValidationResult(TradingBase, CreatedAtMixin):
    """每个 Validator 一条结果（UTC 月 RANGE 分区）。"""

    __tablename__ = "ai_validation_results"
    __table_args__ = (
        PrimaryKeyConstraint("id", "occurred_at", name="pk_ai_validation_results"),
        UniqueConstraint(
            "invocation_id", "invocation_occurred_at", "validator_name", "occurred_at",
            name="uq_ai_validation_results_invocation_validator",
        ),
        ForeignKeyConstraint(
            ["invocation_id", "invocation_occurred_at"],
            ["trading.ai_invocations.id", "trading.ai_invocations.occurred_at"],
            name="fk_ai_validation_results_invocation",
        ),
        CheckConstraint(
            "severity IN ('hard','soft')",
            name="ck_ai_validation_results_severity_known",
        ),
        CheckConstraint(
            "validator_name <> ''",
            name="ck_ai_validation_results_name_nonempty",
        ),
        Index(
            "ix_ai_validation_results_invocation",
            "invocation_id",
            "invocation_occurred_at",
            "occurred_at",
        ),
        {"schema": TRADING_SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(utc_timestamp_type(), nullable=False, primary_key=True)
    invocation_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    invocation_occurred_at: Mapped[datetime] = mapped_column(
        utc_timestamp_type(), nullable=False
    )
    validator_name: Mapped[str] = mapped_column(String(64), nullable=False)
    validator_version: Mapped[str | None] = mapped_column(String(64))
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(128))
    details_artifact_hash: Mapped[str | None] = mapped_column(sha256_type())
