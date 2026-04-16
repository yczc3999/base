"""文章与关键词多对多关系 (替代原 article_tags)."""
from datetime import datetime
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ArticleKeyword(Base):
    __tablename__ = "article_keywords"

    article_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True,
    )
    keyword_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("keywords.id", ondelete="CASCADE"), primary_key=True,
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
