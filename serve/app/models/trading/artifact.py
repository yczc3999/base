"""Trading artifact 域 foundation models（WP-01A-02）。

- ``artifact_objects``：无损表达现有 ``ArtifactRef``（sha256、original/stored size、mime、
  compression、storage driver/version、locator）；唯一键 ``(sha256, compression,
  storage_driver, storage_version)``，尺寸非负，raw（compression='none'）时两尺寸相等。
- ``artifact_lineage_edges``：from/to artifact、受控 relation、可空 invocation ref；
  禁止 self-edge，重复 edge 由唯一约束拒绝。lineage 是纯关系 junction，FK 用 ``CASCADE``。
- ``archive_manifests`` / ``retention_manifests``：canonical JSONB + content hash + status +
  created_at，append-only（无 updated_at）。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import ForeignKey, Index

from app.models.trading.constants import TRADING_SCHEMA
from app.models.trading.mixins import (
    BigIntIdentityMixin,
    CreatedAtMixin,
    TimestampMixin,
    TradingBase,
)
from app.models.trading.types import base_unit_type, external_id_type, sha256_type


class ArtifactObject(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """已存储 artifact 的不可变元数据（content-addressed）。"""

    __tablename__ = "artifact_objects"
    __table_args__ = (
        UniqueConstraint(
            "sha256", "compression", "storage_driver", "storage_version",
            name="uq_artifact_objects_storage_key",
        ),
        CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_artifact_objects_sha256_lower_hex",
        ),
        CheckConstraint("original_size >= 0", name="ck_artifact_objects_original_size_nonneg"),
        CheckConstraint("stored_size >= 0", name="ck_artifact_objects_stored_size_nonneg"),
        CheckConstraint(
            "compression <> 'none' OR original_size = stored_size",
            name="ck_artifact_objects_raw_size_equal",
        ),
        CheckConstraint(
            "compression IN ('none','zstd')",
            name="ck_artifact_objects_compression_known",
        ),
        CheckConstraint(
            "storage_driver IN ('local','s3')",
            name="ck_artifact_objects_storage_driver_known",
        ),
        CheckConstraint(
            "storage_version = 'cas/v1'",
            name="ck_artifact_objects_storage_version_known",
        ),
        CheckConstraint(
            "length(mime) > 0 AND position(chr(10) in mime) = 0 "
            "AND position(chr(13) in mime) = 0",
            name="ck_artifact_objects_mime_valid",
        ),
        CheckConstraint(
            "locator = 'cas/v1/sha256/' || substr(sha256,1,2) || '/' || "
            "substr(sha256,3,2) || '/' || sha256 || "
            "CASE WHEN compression = 'zstd' THEN '.zst' ELSE '.raw' END",
            name="ck_artifact_objects_locator_canonical",
        ),
        {"schema": TRADING_SCHEMA},
    )

    sha256: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    original_size: Mapped[Decimal] = mapped_column(base_unit_type(), nullable=False)
    stored_size: Mapped[Decimal] = mapped_column(base_unit_type(), nullable=False)
    mime: Mapped[str] = mapped_column(String(255), nullable=False)
    compression: Mapped[str] = mapped_column(String(32), nullable=False, server_default="none")
    storage_driver: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_version: Mapped[str] = mapped_column(external_id_type(), nullable=False)
    locator: Mapped[str] = mapped_column(Text, nullable=False)


class ArtifactLineageEdge(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """artifact 派生/关系边；纯关系 junction，FK 用 CASCADE。"""

    __tablename__ = "artifact_lineage_edges"
    __table_args__ = (
        UniqueConstraint(
            "from_artifact_id", "to_artifact_id", "relation",
            name="uq_artifact_lineage_edges_edge",
        ),
        CheckConstraint(
            "from_artifact_id <> to_artifact_id",
            name="ck_artifact_lineage_edges_no_self",
        ),
        CheckConstraint(
            "relation IN ('derived','supersedes','references','raw')",
            name="ck_artifact_lineage_edges_relation_known",
        ),
        {"schema": TRADING_SCHEMA},
    )

    from_artifact_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "trading.artifact_objects.id",
            ondelete="CASCADE",
            name="fk_artifact_lineage_edges_from_artifact",
        ),
        nullable=False,
    )
    to_artifact_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "trading.artifact_objects.id",
            ondelete="CASCADE",
            name="fk_artifact_lineage_edges_to_artifact",
        ),
        nullable=False,
    )
    relation: Mapped[str] = mapped_column(String(64), nullable=False)
    invocation_ref: Mapped[str | None] = mapped_column(external_id_type())


class ArchiveManifest(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """归档 manifest：canonical JSONB + hash + status，append-only。"""

    __tablename__ = "archive_manifests"
    __table_args__ = (
        CheckConstraint(
            "jsonb_typeof(content) = 'object'",
            name="ck_archive_manifests_content_object",
        ),
        CheckConstraint(
            "status IN ('staged','committed')",
            name="ck_archive_manifests_status_known",
        ),
        Index("ix_archive_manifests_content_hash", "content_hash"),
        {"schema": TRADING_SCHEMA},
    )

    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="staged")


class RetentionManifest(TradingBase, BigIntIdentityMixin, CreatedAtMixin):
    """保留策略 manifest：canonical JSONB + hash + status，append-only。"""

    __tablename__ = "retention_manifests"
    __table_args__ = (
        CheckConstraint(
            "jsonb_typeof(content) = 'object'",
            name="ck_retention_manifests_content_object",
        ),
        CheckConstraint(
            "status IN ('staged','committed')",
            name="ck_retention_manifests_status_known",
        ),
        Index("ix_retention_manifests_content_hash", "content_hash"),
        {"schema": TRADING_SCHEMA},
    )

    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(sha256_type(), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="staged")
