"""文章与关键词多对多关系 (替代原 article_tags)."""
from datetime import datetime
from typing import TYPE_CHECKING
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.article import Article
    from app.models.keyword import Keyword


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

    # 关系：关联行 → 文章 / 关键词（只读导航, 读 is_primary 等 payload 时用；
    # 写入走 Article.keywords / Keyword.articles 的 secondary M2M）
    article: Mapped["Article"] = relationship(viewonly=True)
    keyword: Mapped["Keyword"] = relationship(viewonly=True)
