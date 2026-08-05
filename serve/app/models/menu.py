from enum import IntEnum
from datetime import datetime
from typing import TYPE_CHECKING
from sqlalchemy import Integer, String, SmallInteger, Boolean, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base

if TYPE_CHECKING:
    from app.models.role_menu import RoleMenu


class Menu(Base):
    __tablename__ = "menus"

    class Type(IntEnum):
        DIRECTORY = 0   # 目录
        MENU = 1        # 菜单页面
        BUTTON = 2      # 按钮权限

    class Status(IntEnum):
        DISABLED = 0
        ACTIVE = 1

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    parent_id: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    type: Mapped[int] = mapped_column(SmallInteger, default=Type.DIRECTORY, nullable=False)
    slug: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(50), nullable=False)
    icon: Mapped[str | None] = mapped_column(String(100))
    path: Mapped[str | None] = mapped_column(String(200))
    template_path: Mapped[str | None] = mapped_column(String(200))
    redirect: Mapped[str | None] = mapped_column(String(200))
    perms: Mapped[str | None] = mapped_column(String(100))
    link: Mapped[str | None] = mapped_column(String(500))
    link_target: Mapped[str | None] = mapped_column(String(10), default="_self")
    is_cache: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_affix: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_visible: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    badge: Mapped[str | None] = mapped_column(String(20))
    sort: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[int] = mapped_column(SmallInteger, default=Status.ACTIVE, nullable=False)
    remark: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    # 关系：自引用（目录/子菜单）+ 关联表访问 roles
    parent = relationship(
        "Menu",
        remote_side="Menu.id",
        back_populates="children",
        primaryjoin="foreign(Menu.parent_id) == Menu.id",
        viewonly=True,
    )
    children: Mapped[list["Menu"]] = relationship(
        back_populates="parent",
        primaryjoin="Menu.id == foreign(Menu.parent_id)",
        viewonly=True,
    )
    roles_assoc: Mapped[list["RoleMenu"]] = relationship(back_populates="menu", passive_deletes=True)
