"""
ExportHelper 测试 — 单文件导出流式化 / 空数据 / 进度

测试策略：SQLite 内存库 + 简单 model，验证导出生成 xlsx 文件
"""

import pytest
import pytest_asyncio
from pathlib import Path
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Integer

from app.utils import export_helper
from app.utils.export_helper import ExportHelper


class EBase(DeclarativeBase):
    pass


class EUser(EBase):
    __tablename__ = "e_users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    status: Mapped[int] = mapped_column(Integer, default=0)


@pytest_asyncio.fixture
async def engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(EBase.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db(engine):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        yield session


@pytest_asyncio.fixture
async def seed(db):
    for i in range(5):
        db.add(EUser(name=f"user{i}", status=i % 2))
    await db.commit()


@pytest_asyncio.fixture
async def export_redis(monkeypatch, mock_redis):
    """patch export_helper 模块级 get_redis"""
    async def _get_redis():
        return mock_redis
    monkeypatch.setattr(export_helper, "get_redis", _get_redis)
    return mock_redis


def _make_helper(file_key):
    return ExportHelper(
        model=EUser,
        fields=["id", "name", "status"],
        header_map={"id": "ID", "name": "名称", "status": "状态"},
        file_key=file_key,
    )


@pytest.mark.asyncio
async def test_export_single_file(db, seed, export_redis):
    """单文件导出生成 xlsx"""
    helper = _make_helper("test-export-1")
    path = await helper.export(db)
    assert Path(path).exists()
    assert helper.written_rows == 5
    Path(path).unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_export_empty(db, export_redis):
    """空数据也生成带表头的文件"""
    helper = _make_helper("test-export-empty")
    path = await helper.export(db)
    assert Path(path).exists()
    assert helper.written_rows == 0
    Path(path).unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_export_progress_key(db, seed, export_redis):
    """导出后进度 key 置 100"""
    helper = _make_helper("test-export-progress")
    path = await helper.export(db)
    progress = await export_redis.get("export@test-export-progress")
    assert progress == "100"
    Path(path).unlink(missing_ok=True)
