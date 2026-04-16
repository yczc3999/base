"""Keyword — 候选池 + canonical 标签合并表.

stage 区分生命周期:
    candidate  → 采集/人工/GSC 流入, 待审核或已审未发布
    approved   → 审核通过并发布 (等同老的 "tag")
    archived   → 被忽略/软删除
"""
from datetime import datetime
from sqlalchemy import (
    BigInteger, Integer, String, Boolean, DateTime, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Keyword(Base):
    __tablename__ = "keywords"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # 关键字本体
    keyword: Mapped[str] = mapped_column(String(200), nullable=False)
    keyword_norm: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    slug: Mapped[str | None] = mapped_column(String(200), unique=True)

    # 生命周期
    stage: Mapped[str] = mapped_column(String(16), default="candidate", nullable=False)
    # candidate / approved / archived
    review_status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    # pending / ai_approved / ai_rejected / ai_uncertain / human_approved / human_rejected

    # 采集溯源
    source_code: Mapped[str] = mapped_column(String(32), nullable=False)
    seed_keyword: Mapped[str | None] = mapped_column(String(200))
    expanded_as_seed_at: Mapped[datetime | None] = mapped_column(DateTime)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    # GSC 回流指标 + AI 审核结果 (JSONB, 灵活扩展)
    metrics_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    ai_review_json: Mapped[dict | None] = mapped_column(JSONB)

    # Canonical 补充字段 (stage=approved 时才填)
    color: Mapped[str | None] = mapped_column(String(20))
    description: Mapped[str | None] = mapped_column(String(200))
    sort: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    article_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False,
    )
