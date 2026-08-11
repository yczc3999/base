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
    String,
    UniqueConstraint,
)
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
