"""
P1-1/P1-2 测试 — 会话枚举 + 缓存统计/清理

覆盖:
  1. session_list 枚举 Redis 中活跃 token
  2. session_kick 撤销用户全部 session
  3. cache_stats 按前缀统计 key 数 + dbsize
  4. cache_clear 白名单 + 清理指定前缀
"""

import pytest

from app.utils import token as token_mod


class _FakeRequest:
    """最小化 request 替身（只支持 await json()）"""
    def __init__(self, body: dict):
        self._body = body

    async def json(self):
        return self._body


# ==================== 会话管理 ====================

@pytest.mark.asyncio
async def test_session_list_enumerates_tokens(mock_redis):
    await token_mod.create_token_pair(user_id=1, scope="admin", user_info={"username": "root"})
    await token_mod.create_token_pair(user_id=5, scope="client", user_info={"username": "client5"})

    from app.controllers.admin.session import session_list
    resp = await session_list(auth=None)
    sessions = resp["data"]

    assert len(sessions) == 2
    scopes = {(s["scope"], s["user_id"]) for s in sessions}
    assert ("admin", 1) in scopes
    assert ("client", 5) in scopes
    # username 从 token 详情还原
    by_user = {s["user_id"]: s["username"] for s in sessions}
    assert by_user[5] == "client5"
    assert all(s["ttl"] > 0 for s in sessions)


@pytest.mark.asyncio
async def test_session_kick_revokes_all(mock_redis):
    await token_mod.create_token_pair(user_id=7, scope="client", user_info={"username": "u7"})

    from app.controllers.admin.session import session_kick
    await session_kick(_FakeRequest({"scope": "client", "user_id": 7}), auth=None)

    # 索引被删, 用户无活跃 token
    from app.services.redis import get_redis
    r = await get_redis()
    assert await r.smembers(f"{token_mod._PREFIX}:user_tokens:client:7") == set()


# ==================== 缓存管理 ====================

@pytest.mark.asyncio
async def test_cache_stats_counts_prefixes(mock_redis):
    from app.controllers.admin.cache import cache_stats
    await mock_redis.set("dict:id:1", "{}")
    await mock_redis.set("dict:id:2", "{}")
    await mock_redis.set("user:id:5", "{}")
    await mock_redis.set("settings:all", "{}")

    resp = await cache_stats(auth=None)
    payload = resp["data"]
    modules = {m["prefix"]: m["keys"] for m in payload["modules"]}
    assert modules.get("dict", 0) == 2
    assert modules.get("user", 0) == 1
    assert modules.get("setting", 0) == 1  # settings:all 也被统计
    assert payload["dbsize"] == 4


@pytest.mark.asyncio
async def test_cache_clear_removes_prefix(mock_redis):
    from app.controllers.admin.cache import cache_clear
    await mock_redis.set("dict:id:1", "{}")
    await mock_redis.set("dict:id:2", "{}")
    await mock_redis.set("user:id:5", "{}")  # 不应被清

    await cache_clear(_FakeRequest({"prefix": "dict"}), auth=None)
    assert await mock_redis.get("dict:id:1") is None
    assert await mock_redis.get("dict:id:2") is None
    assert await mock_redis.get("user:id:5") is not None


@pytest.mark.asyncio
async def test_cache_clear_rejects_unknown_prefix(mock_redis):
    from app.controllers.admin.cache import cache_clear
    from app.utils.response import ok, fail
    # 未知前缀 → 返回 fail (但不会抛异常)
    result = await cache_clear(_FakeRequest({"prefix": "nope"}), auth=None)
    assert result["code"] != 0
