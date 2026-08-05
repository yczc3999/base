from enum import IntEnum
from datetime import datetime
from typing import TYPE_CHECKING
from sqlalchemy import Integer, String, SmallInteger, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base

if TYPE_CHECKING:
    from app.models.role_menu import RoleMenu
    from app.models.admin_user_role import AdminUserRole


class Role(Base):
    __tablename__ = "roles"

    class Status(IntEnum):
        DISABLED = 0
        ACTIVE = 1

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(50), nullable=False)
    remark: Mapped[str | None] = mapped_column(String(255))
    sort: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[int] = mapped_column(SmallInteger, default=Status.ACTIVE, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    # 关系：通过关联表访问 menus / admin_users（passive_deletes 依赖 DB 级联）
    menus_assoc: Mapped[list["RoleMenu"]] = relationship(back_populates="role", passive_deletes=True)
    users_assoc: Mapped[list["AdminUserRole"]] = relationship(back_populates="role", passive_deletes=True)
