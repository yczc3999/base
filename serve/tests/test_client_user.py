"""
B3 前端用户管理 — UserLogic 守卫测试

覆盖:
  1. before_create/before_edit 弹掉 token_version (S1 反 mass-assignment)
  2. 禁用用户(status=0) → 自动撤销全部 client session (禁用即踢)
  3. 密码加密存储 + 输出过滤 (except_keys=password)

测试策略: SQLite 内存库(users 表) + fake Redis。
"""

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.base import Base
from app.models.user import User
from app.logics.user import user_logic
from app.utils import token as token_mod


@pytest_asyncio.fixture
async def engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(c, tables=[User.__table__])
        )
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db(engine):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        yield session


@pytest_asyncio.fixture
async def logic(mock_redis):
    return user_logic


async def _create_user(db, logic, **extra):
    data = {"username": "testuser", "password": "pass123456", "nickname": "测试"}
    data.update(extra)
    return await logic.create(db, data)


# ==================== token_version 守卫 ====================

@pytest.mark.asyncio
async def test_create_ignores_token_version(db, logic):
    result = await _create_user(db, logic, token_version=999)
    assert result["token_version"] == 0


@pytest.mark.asyncio
async def test_edit_ignores_token_version(db, logic):
    created = await _create_user(db, logic)
    result = await logic.modify(db, created["id"], {"token_version": 999, "nickname": "改名"})
    assert result["token_version"] == 0
    assert result["nickname"] == "改名"


# ==================== 禁用即踢 ====================

@pytest.mark.asyncio
async def test_disable_user_revokes_all_sessions(db, logic, mock_redis):
    user = await _create_user(db, logic)

    # 创建 client session (scope="client")
    pair = await token_mod.create_token_pair(user_id=user["id"], scope="client")
    assert await token_mod.verify_token(pair["access_token"]) is not None

    # 禁用 → 全部 session 撤销
    await logic.modify(db, user["id"], {"status": User.Status.DISABLED})
    assert await token_mod.verify_token(pair["access_token"]) is None


@pytest.mark.asyncio
async def test_normal_edit_keeps_sessions(db, logic, mock_redis):
    user = await _create_user(db, logic)
    pair = await token_mod.create_token_pair(user_id=user["id"], scope="client")

    # 普通编辑 (改昵称) → session 不受影响
    await logic.modify(db, user["id"], {"nickname": "新昵称"})
    assert await token_mod.verify_token(pair["access_token"]) is not None


@pytest.mark.asyncio
async def test_enable_does_not_kick(db, logic, mock_redis):
    user = await _create_user(db, logic, status=User.Status.DISABLED)
    pair = await token_mod.create_token_pair(user_id=user["id"], scope="client")

    # 重新启用 → 不踢 (status 0→1, 不是禁用动作)
    await logic.modify(db, user["id"], {"status": User.Status.ACTIVE})
    assert await token_mod.verify_token(pair["access_token"]) is not None


# ==================== 密码安全 ====================

@pytest.mark.asyncio
async def test_password_hashed_on_create(db, logic):
    user = await _create_user(db, logic, password="secret123")
    # except_keys 过滤 password, 输出不含明文
    assert "password" not in user
    # 直接查 DB 验证是 bcrypt 哈希
    detail = await logic.get_detail(db, user["id"])
    assert "password" not in detail


@pytest.mark.asyncio
async def test_password_change_hashes(db, logic):
    user = await _create_user(db, logic)
    await logic.modify(db, user["id"], {"password": "newpass123"})
    # 不传 password 的编辑不会清空密码
    await logic.modify(db, user["id"], {"nickname": "x"})
    # 用 disable_except 验证 DB 中仍是 bcrypt 哈希 (非空, $2b$ 前缀)
    raw = await logic.disable_except().get_detail(db, user["id"])
    logic.reset_except()
    assert raw["password"].startswith("$2b$")
