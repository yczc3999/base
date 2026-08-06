from datetime import datetime
from typing import TYPE_CHECKING
from sqlalchemy import Integer, String, Boolean, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base

if TYPE_CHECKING:
    from app.models.user import User


class File(Base):
    __tablename__ = "files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    original_name: Mapped[str] = mapped_column(String(200), nullable=False)
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ext: Mapped[str | None] = mapped_column(String(20))
    is_private: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    user_id: Mapped[int | None] = mapped_column(Integer, index=True)
    category: Mapped[str | None] = mapped_column(String(50), default="default", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False, index=True)

    # 关系：上传用户 (viewonly — user_id 无 DB FK 约束, 只读导航)
    user: Mapped["User | None"] = relationship(
        primaryjoin="File.user_id == User.id",
        foreign_keys=[user_id],
        viewonly=True,
    )
