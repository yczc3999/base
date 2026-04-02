from enum import IntEnum
from datetime import datetime
from sqlalchemy import Integer, String, SmallInteger, Boolean, Text, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class Message(Base):
    __tablename__ = "messages"

    class Type(IntEnum):
        SYSTEM = 0      # 系统通知
        APPROVAL = 1    # 审批消息
        ALERT = 2       # 告警消息

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    type: Mapped[int] = mapped_column(SmallInteger, default=Type.SYSTEM, nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sender_id: Mapped[int | None] = mapped_column(Integer)
    sender_name: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
