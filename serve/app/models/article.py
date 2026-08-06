"""文章"""
from datetime import datetime
from typing import TYPE_CHECKING
from sqlalchemy import Integer, BigInteger, String, SmallInteger, Boolean, Text, DateTime, JSON, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base

if TYPE_CHECKING:
    from app.models.keyword import Keyword


class Article(Base):
    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str | None] = mapped_column(String(200), unique=True)
    summary: Mapped[str | None] = mapped_column(String(500))
    excerpt: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    cover_image: Mapped[str | None] = mapped_column(String(500))
    author_id: Mapped[int | None] = mapped_column(Integer)
    view_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sort: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    source: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    # source: 0=手动 1=采集
    source_url: Mapped[str | None] = mapped_column(String(500))
    raw_content: Mapped[str | None] = mapped_column(Text)
    ai_processed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)  # 0=草稿 1=已发布
    published_at: Mapped[datetime | None] = mapped_column(DateTime)
    # === SEO 模块扩展 ===
    simhash: Mapped[int | None] = mapped_column(BigInteger)  # 64-bit 内容指纹
    slug_history: Mapped[list | None] = mapped_column(JSON, default=list)  # ["old-slug",...] 用于 301
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)  # 软删除（访问旧 URL 时返 410）
    # === v2 简化：scheduled_at 替代 publish_schedule 表 ===
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime)
    retry_count: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    last_publish_error: Mapped[str | None] = mapped_column(Text)
    # === 时间戳 ===
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    # 关系：通过 article_keywords 多对多访问 keywords（C1b 关系可导航）
    keywords: Mapped[list["Keyword"]] = relationship(
        secondary="article_keywords",
        back_populates="articles",
        passive_deletes=True,
    )
