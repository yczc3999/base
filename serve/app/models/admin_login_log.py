from enum import IntEnum
from datetime import datetime
from sqlalchemy import Integer, String, SmallInteger, Text, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class AdminLoginLog(Base):
    __tablename__ = "admin_login_logs"

    class Status(IntEnum):
        FAIL = 0       # 登录失败
        SUCCESS = 1    # 登录成功

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(50), nullable=False)
    ip: Mapped[str | None] = mapped_column(String(50))
    user_agent: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[int] = mapped_column(SmallInteger, default=Status.SUCCESS, nullable=False)
    remark: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False, index=True)
