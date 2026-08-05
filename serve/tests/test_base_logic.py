"""
BaseLogic 测试 — CRUD / 软删除 / 缓存 / 分页

测试策略：SQLite 内存库 + fake Redis。所有测试离线运行。
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Integer, DateTime

from app.logics.base import BaseLogic


# ==================== 测试模型 ====================

class TestBase(DeclarativeBase):
    pass


class TestUser(TestBase):
    __tablename__ = "test_users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    email: Mapped[str | None] = mapped_column(String(100), default=None)
    status: Mapped[int] = mapped_column(Integer, default=0)
    deleted_at: Mapped[DateTime | None] = mapped_column(DateTime, default=None)


class TestUserLogic(BaseLogic):
    model = TestUser
    cache_prefix = "test_user"
    cache_fields = ["email"]
    except_keys = []
    bind_user_column = ""

    def allowed_filters(self):
        return ["id", "name", "email", "status"]

    def allowed_sorts(self):
        return ["id", "created_at", "updated_at", "status"]

    def keyword_fields(self):
        return ["name", "email"]


@pytest_asyncio.fixture
async def engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(TestBase.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db(engine):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        yield session


@pytest_asyncio.fixture
async def logic(mock_redis_cache):
    return TestUserLogic()


# ==================== CRUD ====================

@pytest.mark.asyncio
async def test_create(db, logic):
    result = await logic.create(db, {"name": "张三", "email": "a@b.com", "status": 1})
    assert result["id"] is not None
    assert result["name"] == "张三"


@pytest.mark.asyncio
async def test_get_detail(db, logic):
    created = await logic.create(db, {"name": "李四", "email": "l@b.com"})
    detail = await logic.get_detail(db, created["id"])
    assert detail is not None
    assert detail["name"] == "李四"


@pytest.mark.asyncio
async def test_modify(db, logic):
    created = await logic.create(db, {"name": "王五", "email": "w@b.com"})
    updated = await logic.modify(db, created["id"], {"name": "王五改"})
    assert updated["name"] == "王五改"


@pytest.mark.asyncio
async def test_delete_hard(db, logic):
    created = await logic.create(db, {"name": "赵六"})
    await logic.do_delete(db, [created["id"]])
    detail = await logic.get_detail(db, created["id"])
    assert detail is None


@pytest.mark.asyncio
async def test_soft_delete_hides_from_list(db, logic):
    """软删：do_delete 置 deleted_at，列表不再显示"""
    created = await logic.create(db, {"name": "钱七"})
    await logic.do_delete(db, [created["id"]])

    result = await logic.get_list(db, {})
    assert result["total"] == 0


@pytest.mark.asyncio
async def test_soft_delete_hides_from_detail(db, logic):
    """软删：get_detail 对已删记录返回 None"""
    created = await logic.create(db, {"name": "孙八"})
    await logic.do_delete(db, [created["id"]])

    detail = await logic.get_detail(db, created["id"])
    assert detail is None


@pytest.mark.asyncio
async def test_soft_delete_keeps_row_in_db(db, logic):
    """软删：记录还在 DB（deleted_at 非空），只是被过滤"""
    created = await logic.create(db, {"name": "周九"})
    await logic.do_delete(db, [created["id"]])

    from sqlalchemy import select
    result = await db.execute(select(TestUser).where(TestUser.id == created["id"]))
    row = result.scalar_one()
    assert row.deleted_at is not None


# ==================== 分页 ====================

@pytest.mark.asyncio
async def test_pagination(db, logic):
    for i in range(25):
        await logic.create(db, {"name": f"user{i}"})
    result = await logic.get_list(db, {"page": 1, "pageSize": 10})
    assert result["total"] == 25
    assert len(result["list"]) == 10


@pytest.mark.asyncio
async def test_page_size_capped_at_100(db, logic):
    result = await logic.get_list(db, {"pageSize": 999})
    assert result["pageSize"] == 100


@pytest.mark.asyncio
async def test_page_beyond_max_raises(db, logic):
    from app.logics.base import BizError
    with pytest.raises(BizError):
        await logic.get_list(db, {"page": 100001})


# ==================== 白名单 ====================

@pytest.mark.asyncio
async def test_filter_whitelist(db, logic):
    """非白名单字段的过滤条件被忽略"""
    await logic.create(db, {"name": "aa", "status": 1})
    result = await logic.get_list(db, {"filters": {"nonexistent": 1}})
    assert result["total"] >= 1


# ==================== 缓存 ====================

@pytest.mark.asyncio
async def test_cache_set_on_get_detail(db, logic, mock_redis_cache):
    created = await logic.create(db, {"name": "缓存用户", "email": "cache@b.com"})
    await logic.get_detail(db, created["id"])
    # 缓存应有该 key
    key = f"test_user:id:{created['id']}"
    val = await mock_redis_cache.get(key)
    assert val is not None


@pytest.mark.asyncio
async def test_negative_cache_on_missing_detail(db, logic, mock_redis_cache):
    """不存在的 id 命中负缓存（第二次不查 DB 直接返回 None）"""
    # 第一次：无缓存，走 DB，写负缓存
    detail = await logic.get_detail(db, 99999)
    assert detail is None
    key = f"test_user:id:99999"
    raw = await mock_redis_cache.get(key)
    import json as _json
    cached = _json.loads(raw)
    assert cached.get("__null__") is True
    # 第二次：负缓存命中，直接返回 None
    detail2 = await logic.get_detail(db, 99999)
    assert detail2 is None


@pytest.mark.asyncio
async def test_negative_cache_cleared_on_create(db, logic, mock_redis_cache):
    """负缓存被创建操作覆盖（create 写正缓存到同一 email key）"""
    # 先查不存在的 email → 写负缓存
    assert await logic.get_by_field(db, "email", "new@b.com") is None
    # 创建该 email 用户 → 正缓存覆盖负缓存（email 在 cache_fields 里）
    created = await logic.create(db, {"name": "新用户", "email": "new@b.com"})
    # 再次按 email 查 → 应命中正缓存返回用户
    found = await logic.get_by_field(db, "email", "new@b.com")
    assert found is not None
    assert found["id"] == created["id"]


@pytest.mark.asyncio
async def test_cache_invalidated_on_modify(db, logic, mock_redis_cache):
    """modify 后 get_detail 返回新值（缓存已失效/重建）"""
    created = await logic.create(db, {"name": "原值", "email": "orig@b.com"})
    await logic.get_detail(db, created["id"])
    await logic.modify(db, created["id"], {"name": "新值"})
    detail = await logic.get_detail(db, created["id"])
    assert detail["name"] == "新值"


# ==================== save 合一 ====================

@pytest.mark.asyncio
async def test_save_create_and_modify(db, logic):
    created = await logic.save(db, {"name": "合一体"})
    assert created["id"] is not None
    updated = await logic.save(db, {"id": created["id"], "name": "合一体改"})
    assert updated["name"] == "合一体改"
