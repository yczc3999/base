"""DbBackup — 数据库备份记录.

记录每次 pg_dump 的元信息。文件本体存在 storage/backups/ 目录。
"""
from datetime import datetime
from sqlalchemy import Integer, BigInteger, String, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class DbBackup(Base):
    __tablename__ = "db_backups"

    class Status:
        OK = "ok"
        FAILED = "failed"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=Status.OK, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    error_msg: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
