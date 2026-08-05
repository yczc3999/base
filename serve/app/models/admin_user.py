from enum import IntEnum
from datetime import datetime
from typing import TYPE_CHECKING
from sqlalchemy import Integer, String, SmallInteger, Boolean, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base

if TYPE_CHECKING:
    from app.models.admin_user_role import AdminUserRole


class AdminUser(Base):
    __tablename__ = "admin_users"

    class Status(IntEnum):
        DISABLED = 0   # 禁用
        ACTIVE = 1     # 正常

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    password: Mapped[str] = mapped_column(String(255), nullable=False)
    nickname: Mapped[str | None] = mapped_column(String(50))
    avatar: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(100))
    phone: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[int] = mapped_column(SmallInteger, default=Status.ACTIVE, nullable=False)
    is_super_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    token_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_login_ip: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    # 关系：通过关联表访问 roles（passive_deletes 依赖 DB 级联）
    roles_assoc: Mapped[list["AdminUserRole"]] = relationship(back_populates="user", passive_deletes=True)
