"""
Dict / DictItem 逻辑测试 — CRUD / 公开查询 / 缓存失效 / FK 级联

测试策略：SQLite 内存库（显式外键）+ fake Redis。离线运行。
"""

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.base import Base
from app.models.dict import Dict, DictItem
from app.logics.dict import DictLogic, DictItemLogic, ITEMS_CACHE_PREFIX
from app.logics.base import BizError


@pytest_asyncio.fixture
async def engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA foreign_keys=ON"))
        # 只建 dict 相关表（共享 metadata 含 JSONB 的 keyword 表, SQLite 无法编译）
        await conn.run_sync(
            lambda c: Base.metadata.create_all(c, tables=[Dict.__table__, DictItem.__table__])
        )
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db(engine):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        yield session


@pytest_asyncio.fixture
async def logic(mock_redis_cache):
    return DictLogic()


@pytest_asyncio.fixture
async def item_logic(mock_redis_cache):
    return DictItemLogic()


# ==================== Dict CRUD ====================

@pytest.mark.asyncio
async def test_create_dict(db, logic):
    result = await logic.create(db, {"type_name": "gender", "description": "性别"})
    assert result["id"] is not None
    assert result["type_name"] == "gender"


@pytest.mark.asyncio
async def test_duplicate_type_name_rejected(db, logic):
    await logic.create(db, {"type_name": "gender"})
    with pytest.raises(BizError):
        await logic.create(db, {"type_name": "gender"})


@pytest.mark.asyncio
async def test_create_dict_validates_type_name(db, logic):
    with pytest.raises(BizError):
        await logic.create(db, {"type_name": ""})


# ==================== DictItem CRUD + 级联 ====================

@pytest.mark.asyncio
async def test_create_item_and_get_items(db, logic, item_logic):
    d = await logic.create(db, {"type_name": "gender"})
    await item_logic.create(db, {"dict_id": d["id"], "value": "1", "label": "男", "sort": 1})
    await item_logic.create(db, {"dict_id": d["id"], "value": "2", "label": "女", "sort": 2})

    items = await logic.get_items_by_type(db, "gender")
    assert items == [{"value": "1", "label": "男"}, {"value": "2", "label": "女"}]


@pytest.mark.asyncio
async def test_items_sorted_by_sort_then_id(db, logic, item_logic):
    d = await logic.create(db, {"type_name": "level"})
    await item_logic.create(db, {"dict_id": d["id"], "value": "a", "label": "A", "sort": 3})
    await item_logic.create(db, {"dict_id": d["id"], "value": "b", "label": "B", "sort": 1})
    items = await logic.get_items_by_type(db, "level")
    assert [i["value"] for i in items] == ["b", "a"]


@pytest.mark.asyncio
async def test_disabled_items_excluded(db, logic, item_logic):
    d = await logic.create(db, {"type_name": "status"})
    await item_logic.create(db, {"dict_id": d["id"], "value": "1", "label": "启用", "status": 1})
    await item_logic.create(db, {"dict_id": d["id"], "value": "0", "label": "禁用", "status": 0})
    items = await logic.get_items_by_type(db, "status")
    assert [i["value"] for i in items] == ["1"]


@pytest.mark.asyncio
async def test_delete_dict_cascades_items(db, logic, item_logic):
    d = await logic.create(db, {"type_name": "gender"})
    await item_logic.create(db, {"dict_id": d["id"], "value": "1", "label": "男"})
    await item_logic.create(db, {"dict_id": d["id"], "value": "2", "label": "女"})

    await logic.do_delete(db, [d["id"]])

    items = await logic.get_items_by_type(db, "gender")
    assert items == []


# ==================== 缓存 ====================

@pytest.mark.asyncio
async def test_get_items_cached(db, logic, item_logic, mock_redis):
    d = await logic.create(db, {"type_name": "gender"})
    await item_logic.create(db, {"dict_id": d["id"], "value": "1", "label": "男"})

    # 首次查询写入缓存
    items = await logic.get_items_by_type(db, "gender")
    assert items == [{"value": "1", "label": "男"}]
    assert await mock_redis.get(f"{ITEMS_CACHE_PREFIX}gender") is not None


@pytest.mark.asyncio
async def test_item_mutation_invalidates_cache(db, logic, item_logic):
    d = await logic.create(db, {"type_name": "gender"})
    await item_logic.create(db, {"dict_id": d["id"], "value": "1", "label": "男"})
    await logic.get_items_by_type(db, "gender")  # 写入缓存

    # 新增一项 → 缓存应失效
    await item_logic.create(db, {"dict_id": d["id"], "value": "2", "label": "女"})
    items = await logic.get_items_by_type(db, "gender")
    assert [i["value"] for i in items] == ["1", "2"]


@pytest.mark.asyncio
async def test_edit_item_invalidates_cache(db, logic, item_logic):
    d = await logic.create(db, {"type_name": "gender"})
    item = await item_logic.create(db, {"dict_id": d["id"], "value": "1", "label": "男"})
    await logic.get_items_by_type(db, "gender")

    await item_logic.modify(db, item["id"], {"label": "先生"})
    items = await logic.get_items_by_type(db, "gender")
    assert items == [{"value": "1", "label": "先生"}]


@pytest.mark.asyncio
async def test_dict_edit_invalidates_cache(db, logic, item_logic):
    d = await logic.create(db, {"type_name": "gender"})
    await item_logic.create(db, {"dict_id": d["id"], "value": "1", "label": "男"})
    await logic.get_items_by_type(db, "gender")

    # 重命名 type_name → 旧 key 应失效
    await logic.modify(db, d["id"], {"type_name": "sex"})
    assert await logic.get_items_by_type(db, "gender") == []
    items = await logic.get_items_by_type(db, "sex")
    assert [i["value"] for i in items] == ["1"]


@pytest.mark.asyncio
async def test_unknown_type_cached_empty(db, logic, mock_redis):
    items = await logic.get_items_by_type(db, "not_exist")
    assert items == []
    assert await mock_redis.get(f"{ITEMS_CACHE_PREFIX}not_exist") is not None
