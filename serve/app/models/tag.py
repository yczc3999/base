"""标签（= SEO 关键词 = 着陆页）"""
from datetime import datetime
from sqlalchemy import Boolean, Integer, String, SmallInteger, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    slug: Mapped[str | None] = mapped_column(String(50), unique=True)
    color: Mapped[str | None] = mapped_column(String(20))
    description: Mapped[str | None] = mapped_column(String(200))
    source: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    # source: 0=手动 1=Google 2=Yandex 3=DuckDuckGo
    seed_keyword: Mapped[str | None] = mapped_column(String(200))
    harvested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime)
    article_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sort: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    # status: 0=待审核 1=上线 2=忽略
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
