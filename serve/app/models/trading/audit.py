"""Trading replay / audit model（WP-04 Checkpoint B，revision ``b1000040``）。

audit.py 只放 ``replay_runs``（任务 §4-B：不得顺手创建未来的 workflow-event、
external-call 或 alert 系统）。

不变量（任务 §5.4）：
- replay 只读原 artifact/snapshot/事实，输出新 replay/ablation/metric artifact；
  相同 manifest+code+seed 重跑 hash 全等。
- ``output_artifact_hash`` 非空（重放必产物）；replay_kind 仅 original/new_code/variant。
- append-only（immutable trigger 复用 0002 ``v2_reject_immutable_row``）。
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.schema import Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.trading.constants import TRADING_SCHEMA
from app.models.trading.mixins import (
    BigIntIdentityMixin,
    CreatedAtMixin,
    TradingBase,
)
from app.models.trading.types import (
    external_id_type,
    sha256_type,
    utc_timestamp_type,
)

REPLAY_KINDS = ("original", "new_code", "variant")
_ALERT_SEVERITY = ("INFO", "WARNING", "ERROR", "CRITICAL")


class ReplayRun(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """一次科学回放（append-only）。"""

    __tablename__ = "replay_runs"
    __table_args__ = (
        UniqueConstraint("run_key", name="uq_replay_runs_key"),
        CheckConstraint(
            "replay_kind IN ('original','new_code','variant')",
            name="ck_replay_runs_kind_known",
        ),
        CheckConstraint(
            "manifest_hash ~ '^[0-9a-f]{64}$'",
            name="ck_replay_runs_manifest_hash_hex",
        ),
        CheckConstraint(
            "code_hash ~ '^[0-9a-f]{64}$'",
            name="ck_replay_runs_code_hash_hex",
        ),
        CheckConstraint(
            "input_artifact_hash ~ '^[0-9a-f]{64}$'",
            name="ck_replay_runs_input_hash_hex",
        ),
        CheckConstraint(
            "output_artifact_hash ~ '^[0-9a-f]{64}$'",
            name="ck_replay_runs_output_hash_hex",
        ),
        {"schema": TRADING_SCHEMA},
    )

    run_key: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    replay_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    manifest_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    code_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    seed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    input_artifact_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    output_artifact_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    result: Mapped[dict | None] = mapped_column(JSONB)


# ---------------- WP-05 Checkpoint C：workflow / external-call / alert（append-only） ----------------

_WORKFLOW_AGGREGATE_TYPES = ("envelope", "order", "attempt", "trade", "reconciliation")


class WorkflowEvent(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """append-only 工作流事件（event_key 唯一；payload 只存 hash + redacted content）。"""

    __tablename__ = "workflow_events"
    __table_args__ = (
        UniqueConstraint("event_key", name="uq_workflow_events_key"),
        CheckConstraint(
            "payload_hash ~ '^[0-9a-f]{64}$'",
            name="ck_workflow_events_payload_hash_hex",
        ),
        CheckConstraint(
            "length(btrim(event_type)) > 0",
            name="ck_workflow_events_type_nonempty",
        ),
        CheckConstraint(
            "length(btrim(aggregate_type)) > 0",
            name="ck_workflow_events_aggregate_type_nonempty",
        ),
        Index("ix_workflow_events_aggregate", "aggregate_type", "aggregate_id"),
        {"schema": TRADING_SCHEMA},
    )

    event_key: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    payload_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)


class ExternalCallAttempt(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """append-only 外部调用尝试：只存 endpoint/method/request-response hash/status/latency。

    认证 header、body、signature、secret/passphrase 明文为 0（只存 hash）。
    """

    __tablename__ = "external_call_attempts"
    __table_args__ = (
        UniqueConstraint("attempt_key", name="uq_external_call_attempts_key"),
        CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name="ck_external_call_attempts_request_hash_hex",
        ),
        CheckConstraint(
            "response_hash IS NULL OR response_hash ~ '^[0-9a-f]{64}$'",
            name="ck_external_call_attempts_response_hash_hex",
        ),
        CheckConstraint("latency_ms >= 0", name="ck_external_call_attempts_latency_nonneg"),
        CheckConstraint("fence_token > 0", name="ck_external_call_attempts_fencing_positive"),
        CheckConstraint(
            "method IN ('GET','POST','DELETE','PUT','PATCH')",
            name="ck_external_call_attempts_method_known",
        ),
        CheckConstraint("status_code >= 0", name="ck_external_call_attempts_status_nonneg"),
        Index("ix_external_call_attempts_driver", "driver", "created_at"),
        {"schema": TRADING_SCHEMA},
    )

    attempt_key: Mapped[str] = mapped_column(String(255), nullable=False)
    driver: Mapped[str] = mapped_column(String(64), nullable=False)
    endpoint: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    request_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    response_hash: Mapped[str | None] = mapped_column(sha256_type())
    status_code: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    rate_limit_remaining: Mapped[int | None] = mapped_column(Integer)
    error_reason: Mapped[str | None] = mapped_column(String(128))
    fence_token: Mapped[int] = mapped_column(BigInteger, nullable=False)


class AlertEvent(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """append-only 告警事件（message 只存脱敏文本，绝不存 secret）。"""

    __tablename__ = "alert_events"
    __table_args__ = (
        UniqueConstraint("alert_key", name="uq_alert_events_key"),
        CheckConstraint(
            f"severity IN {tuple(repr(s) for s in _ALERT_SEVERITY)}",
            name="ck_alert_events_severity_known",
        ),
        CheckConstraint("length(btrim(code)) > 0", name="ck_alert_events_code_nonempty"),
        {"schema": TRADING_SCHEMA},
    )

    alert_key: Mapped[str] = mapped_column(String(255), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    code: Mapped[str] = mapped_column(String(128), nullable=False)
    message_redacted: Mapped[str] = mapped_column(Text, nullable=False)
