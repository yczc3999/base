"""
P2-5 回收站测试 — get_trash / restore / purge

覆盖:
  1. do_delete 软删后: get_list 不含, get_trash 含
  2. restore 恢复: 回到 get_list, 从 trash 消失
  3. purge 彻底删除: 物理删除
  4. 非软删除模块 get_trash 拒绝
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Integer, DateTime

from app.logics.base import BaseLogic, BizError


class TestBase(DeclarativeBase):
    pass


class TrashUser(TestBase):
    __tablename__ = "trash_users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    deleted_at: Mapped[DateTime | None] = mapped_column(DateTime, default=None)


class TrashUserLogic(BaseLogic):
    model = TrashUser
    cache_prefix = ""
    except_keys = []

    def keyword_fields(self):
        return ["name"]


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(TestBase.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def logic(mock_redis):
    return TrashUserLogic()


@pytest.mark.asyncio
async def test_soft_delete_moves_to_trash(db, logic):
    a = await logic.create(db, {"name": "甲"})
    b = await logic.create(db, {"name": "乙"})
    await logic.do_delete(db, [a["id"]])

    # get_list 不含已删
    active = await logic.get_list(db, {})
    assert [r["name"] for r in active["list"]] == ["乙"]

    # get_trash 含已删
    trash = await logic.get_trash(db, {})
    assert [r["name"] for r in trash["list"]] == ["甲"]


@pytest.mark.asyncio
async def test_restore_returns_to_active(db, logic):
    a = await logic.create(db, {"name": "甲"})
    await logic.do_delete(db, [a["id"]])
    assert (await logic.get_trash(db, {}))["total"] == 1

    await logic.restore(db, [a["id"]])

    assert (await logic.get_trash(db, {}))["total"] == 0
    active = await logic.get_list(db, {})
    assert "甲" in [r["name"] for r in active["list"]]


@pytest.mark.asyncio
async def test_purge_hard_deletes(db, logic):
    a = await logic.create(db, {"name": "甲"})
    await logic.do_delete(db, [a["id"]])

    await logic.purge(db, [a["id"]])

    assert (await logic.get_trash(db, {}))["total"] == 0
    assert (await logic.get_list(db, {}))["total"] == 0


@pytest.mark.asyncio
async def test_get_trash_rejected_without_deleted_at(db):
    from sqlalchemy.orm import DeclarativeBase as _DB, Mapped as _M, mapped_column as _mc

    class NoSoft(_DB):
        pass

    class Plain(NoSoft):
        __tablename__ = "plain_items"
        id: _M[int] = _mc(Integer, primary_key=True)

    class PlainLogic(BaseLogic):
        model = Plain
        cache_prefix = ""

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(NoSoft.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        with pytest.raises(BizError, match="不支持回收站"):
            await PlainLogic().get_trash(s, {})
        with pytest.raises(BizError, match="不支持回收站"):
            await PlainLogic().restore(s, [1])
    await engine.dispose()
