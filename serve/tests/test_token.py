"""
Token 生命周期测试 — 生成/验证/刷新/登出/全部撤销

测试策略：fake Redis 直接驱动 app.utils.token 的函数（不连真 Redis）
"""

import pytest

from app.utils import token as token_mod


@pytest.mark.asyncio
async def test_create_token_pair(mock_redis):
    result = await token_mod.create_token_pair(user_id=1, scope="admin")
    assert result["access_token"]
    assert result["refresh_token"]
    assert result["expires_in"] > 0
    key = f"{token_mod._PREFIX}:token:{result['access_token']}"
    assert await mock_redis.get(key) is not None


@pytest.mark.asyncio
async def test_verify_token(mock_redis):
    result = await token_mod.create_token_pair(user_id=1, scope="admin")
    info = await token_mod.verify_token(result["access_token"])
    assert info is not None
    assert info["user_id"] == 1
    assert info["scope"] == "admin"


@pytest.mark.asyncio
async def test_verify_invalid_token(mock_redis):
    info = await token_mod.verify_token("nonexistent")
    assert info is None


@pytest.mark.asyncio
async def test_refresh_access_token(mock_redis):
    result = await token_mod.create_token_pair(user_id=1, scope="admin")
    refreshed = await token_mod.refresh_access_token(result["refresh_token"])
    assert refreshed is not None
    assert refreshed["access_token"]
    assert refreshed["access_token"] != result["access_token"]
    # 旧 access_token 应失效
    assert await token_mod.verify_token(result["access_token"]) is None
    # 新 access_token 有效
    assert await token_mod.verify_token(refreshed["access_token"]) is not None


@pytest.mark.asyncio
async def test_refresh_invalid_token(mock_redis):
    result = await token_mod.refresh_access_token("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_revoke_token_removes_access(mock_redis):
    result = await token_mod.create_token_pair(user_id=1, scope="admin")
    await token_mod.revoke_token(result["access_token"])
    assert await token_mod.verify_token(result["access_token"]) is None


@pytest.mark.asyncio
async def test_revoke_token_removes_refresh(mock_redis):
    """登出后 refresh_token 也应失效"""
    result = await token_mod.create_token_pair(user_id=1, scope="admin")
    await token_mod.revoke_token(result["access_token"])
    assert await token_mod.refresh_access_token(result["refresh_token"]) is None


@pytest.mark.asyncio
async def test_revoke_all_tokens_isolates_users(mock_redis):
    """踢人只影响指定用户"""
    u1a = await token_mod.create_token_pair(user_id=1, scope="admin")
    await token_mod.create_token_pair(user_id=1, scope="admin")
    u2 = await token_mod.create_token_pair(user_id=2, scope="admin")

    await token_mod.revoke_all_tokens("admin", 1)
    # user1 的 access 全失效
    assert await token_mod.verify_token(u1a["access_token"]) is None
    # user2 不受影响
    assert await token_mod.verify_token(u2["access_token"]) is not None


@pytest.mark.asyncio
async def test_token_scope_preserved(mock_redis):
    result = await token_mod.create_token_pair(user_id=3, scope="client")
    info = await token_mod.verify_token(result["access_token"])
    assert info["scope"] == "client"
