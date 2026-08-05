"""Dict / DictItem — 数据字典.

集中管理枚举/常量（性别/状态/类型等），前端 DictTag 组件通过
`GET /api/dict/items?type={type_name}` 拉取渲染标签。

dict_items 通过 FK 挂在 dicts 下，删 dict 级联删 items。
"""
from enum import IntEnum
from datetime import datetime
from sqlalchemy import Integer, String, SmallInteger, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base


class Dict(Base):
    __tablename__ = "dicts"

    class Status(IntEnum):
        DISABLED = 0
        ACTIVE = 1

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type_name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[int] = mapped_column(SmallInteger, default=Status.ACTIVE, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    items: Mapped[list["DictItem"]] = relationship(
        back_populates="dict",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="DictItem.sort",
    )


class DictItem(Base):
    __tablename__ = "dict_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dict_id: Mapped[int] = mapped_column(
        ForeignKey("dicts.id", ondelete="CASCADE"), nullable=False
    )
    value: Mapped[str] = mapped_column(String(100), nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    sort: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[int] = mapped_column(SmallInteger, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    dict: Mapped["Dict"] = relationship(back_populates="items")
