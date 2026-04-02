from enum import IntEnum
from datetime import datetime
from sqlalchemy import Integer, String, SmallInteger, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


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
