"""SEO 关键词 — 采集 + 审核 + 转标签"""
from datetime import datetime
from sqlalchemy import Boolean, Integer, String, SmallInteger, Numeric, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class SearchKeyword(Base):
    __tablename__ = "search_keywords"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    keyword: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    clicks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    impressions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ctr: Mapped[float | None] = mapped_column(Numeric(5, 4))
    position: Mapped[float | None] = mapped_column(Numeric(5, 1))
    tag_id: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    # source: 0=manual 1=google_suggest 2=yandex_suggest 3=ddg_suggest
    seed_keyword: Mapped[str | None] = mapped_column(String(200))
    harvested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # harvested: 是否已作为种子词采集过（轮询时跳过已采集的）
    status: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    # status: 0=待审核 1=已上线 2=已忽略
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
