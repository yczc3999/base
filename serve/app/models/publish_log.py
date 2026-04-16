"""SEO 模块的动作时间线。

action 枚举（约定，不 DB 枚举化，保持灵活）：
  collect / rewrite / fingerprint
  schedule / publish / skip
  indexnow / sitemap_rebuild
  phase_change / kill_switch
  manual_approve / manual_cancel
  error

level: info / warn / error
payload: 结构化 JSONB（给工程师），msg: 人类可读句（给管理员面板）。
"""
from datetime import datetime
from sqlalchemy import BigInteger, Integer, String, Text, DateTime, JSON, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class PublishLog(Base):
    __tablename__ = "publish_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    level: Mapped[str] = mapped_column(String(8), default="info", nullable=False)
    article_id: Mapped[int | None] = mapped_column(Integer)
    msg: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
