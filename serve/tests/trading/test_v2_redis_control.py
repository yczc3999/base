"""
WP-00b Control Redis 验收测试。

使用本机真实 Redis（需运行中）做原子性集成测试；每个测试用随机 namespace，
结束后只删除自己创建的精确 key，不 flushdb。
覆盖：pool/namespace 隔离、lease 竞争、非 owner 拒绝、fencing 单调、
lease 过期重取、CAS、Stream、fail-closed、aclose 幂等、无 SCAN/pattern delete。

每个测试用一个 asyncio.run 包住 创建→使用→关闭 完整流程，连接绑定单一临时
loop，避免跨 loop 关闭崩溃，也不依赖 pytest-asyncio 的 loop 生命周期。
"""

import asyncio
from uuid import uuid4

import pytest
from redis.exceptions import RedisError

from app.config import CacheRedisEndpoint, ControlRedisEndpoint
from app.services.redis_cache import CacheRedisClient
from app.services.redis_control import ControlRedisClient, LeaseHandle

HOST = "localhost"
PORT = 6379


def _control_endpoint(url: str | None = None, *, namespace: str | None = None) -> ControlRedisEndpoint:
    return ControlRedisEndpoint(
        url=url or f"redis://{HOST}:{PORT}/0",
        max_connections=5,
        connect_timeout_s=2.0,
        read_timeout_s=2.0,
        health_check_interval_s=30.0,
        namespace=namespace or f"pm:it:{uuid4().hex}:control",
    )


def _cache_endpoint(db: int = 1, *, namespace: str | None = None) -> CacheRedisEndpoint:
    return CacheRedisEndpoint(
        url=f"redis://{HOST}:{PORT}/{db}",
        max_connections=5,
        connect_timeout_s=2.0,
        read_timeout_s=2.0,
        health_check_interval_s=30.0,
        namespace=namespace or f"pm:it:{uuid4().hex}:cache",
        default_ttl_s=300,
        ttl_jitter_s=30,
        bypass_on_error=True,
    )


def _client(endpoint: ControlRedisEndpoint | None = None) -> ControlRedisClient:
    return ControlRedisClient(endpoint or _control_endpoint())


async def _cleanup(c: ControlRedisClient, keys: list[str]) -> None:
    """删除自己创建的精确 key（只删自己，不 SCAN/flush）。"""
    if keys:
        await c._client.delete(*keys)


def _run(coro):
    """单临时 loop 跑完整流程（创建/使用/关闭同 loop）。"""
    return asyncio.run(coro)


# ---------------- pool / namespace 隔离 ----------------

def test_control_and_cache_pools_isolated():
    async def _t():
        control = _client()
        cache = CacheRedisClient(_cache_endpoint())
        try:
            assert control.pool is not cache.pool
            assert control.namespace != cache.namespace
            assert control._endpoint.url.endswith("/0")
            assert cache._endpoint.url.endswith("/1")
        finally:
            await control.aclose()
            await cache.aclose()

    _run(_t())


def test_control_namespace_isolated_from_cache_namespace():
    async def _t():
        control = _client(_control_endpoint(url=f"redis://{HOST}:{PORT}/2"))
        cache = CacheRedisClient(_cache_endpoint(db=2))
        lease_key = f"{control.namespace}:lease:shared"
        try:
            assert await control.acquire_lease("shared", "a", 5.0) is not None
            assert await cache.get("lease:shared", "lease:shared") is None
        finally:
            await _cleanup(control, [lease_key, f"{control.namespace}:fence:shared"])
            await control.aclose()
            await cache.aclose()

    _run(_t())


# ---------------- lease 竞争 / 所有权 / fencing ----------------

def test_lease_contention_and_ownership():
    async def _t():
        c = _client()
        name = "l1"
        try:
            a = await c.acquire_lease(name, "owner_a", 5.0)
            assert a is not None
            assert await c.acquire_lease(name, "owner_b", 5.0) is None  # B 竞争失败
            assert await c.renew_lease(a, 5.0) is True                  # A 可续租
            bogus = LeaseHandle(name, "owner_b", 999999, 5.0)
            assert await c.renew_lease(bogus, 5.0) is False             # 非 owner
            assert await c.release_lease(bogus) is False
            wrong_token = LeaseHandle(name, "owner_a", a.token + 1, 5.0)
            assert await c.release_lease(wrong_token) is False          # token 错
            assert await c.release_lease(a) is True                     # A 释放
            b = await c.acquire_lease(name, "owner_b", 5.0)
            assert b is not None
            assert b.token > a.token                                    # fencing 递增
        finally:
            await _cleanup(c, [f"{c.namespace}:lease:{name}", f"{c.namespace}:fence:{name}"])
            await c.aclose()

    _run(_t())


def test_fencing_token_monotonic_across_owners():
    async def _t():
        c = _client()
        name = "l2"
        tokens = []
        try:
            for owner in ("a", "b", "c"):
                h = await c.acquire_lease(name, owner, 5.0)
                assert h is not None
                tokens.append(h.token)
                assert await c.release_lease(h) is True
            assert tokens == sorted(tokens)
            assert len(set(tokens)) == 3
            assert await c.fencing_token(name) == max(tokens)
        finally:
            await _cleanup(c, [f"{c.namespace}:lease:{name}", f"{c.namespace}:fence:{name}"])
            await c.aclose()

    _run(_t())


def test_lease_reacquire_after_expiry():
    async def _t():
        c = _client()
        name = "l3"
        try:
            a = await c.acquire_lease(name, "a", 0.1)
            assert a is not None
            await asyncio.sleep(0.2)  # 过期
            assert await c.renew_lease(a, 5.0) is False
            assert await c.release_lease(a) is False
            b = await c.acquire_lease(name, "b", 5.0)
            assert b is not None
            assert b.token > a.token
        finally:
            await _cleanup(c, [f"{c.namespace}:lease:{name}", f"{c.namespace}:fence:{name}"])
            await c.aclose()

    _run(_t())


def test_acquire_rejects_non_positive_ttl():
    async def _t():
        c = _client()
        with pytest.raises(ValueError):
            await c.acquire_lease("x", "a", 0)

    _run(_t())


# ---------------- CAS ----------------

def test_cas_success_and_conflict():
    async def _t():
        c = _client()
        name = "cas1"
        key = f"{c.namespace}:cas:{name}"
        try:
            assert await c.compare_and_swap(name, None, "new") is True   # 首次创建
            assert await c.compare_and_swap(name, "old", "x") is False   # 冲突
            assert await c.compare_and_swap(name, "new", "newer", ttl_s=60) is True
            assert await c._client.get(key) == "newer"
        finally:
            await _cleanup(c, [key])
            await c.aclose()

    _run(_t())


# ---------------- Streams ----------------

def test_stream_write_and_read():
    async def _t():
        c = _client()
        name = "s1"
        key = f"{c.namespace}:stream:{name}"
        try:
            mid = await c.stream_add(name, {"a": "1", "b": "2"})
            assert isinstance(mid, str) and "-" in mid
            items = await c.stream_read(name, last_id="0")
            assert len(items) == 1
            assert items[0][0] == mid
            assert items[0][1] == {"a": "1", "b": "2"}
            assert await c.stream_len(name) == 1
            assert await c.stream_trim(name, maxlen=0) == 1
            assert await c.stream_len(name) == 0
        finally:
            await _cleanup(c, [key])
            await c.aclose()

    _run(_t())


# ---------------- fail-closed ----------------

def test_control_fail_closed_when_down():
    async def _t():
        bad = _client(_control_endpoint(url="redis://127.0.0.1:1/0"))
        try:
            with pytest.raises(RedisError):
                await bad.ping()
            with pytest.raises(RedisError):
                await bad.acquire_lease("x", "a", 5.0)
            with pytest.raises(RedisError):
                await bad.compare_and_swap("x", "old", "new")
            with pytest.raises(RedisError):
                await bad.stream_add("x", {"a": "1"})
            h = await bad.health()
            assert h["ok"] is False
            assert h["namespace"] == bad.namespace
        finally:
            await bad.aclose()

    _run(_t())


# ---------------- close 幂等 / 无 SCAN API ----------------

def test_aclose_idempotent():
    async def _t():
        c = _client()
        await c.aclose()
        await c.aclose()  # 二次安全

    _run(_t())


def test_no_scan_or_pattern_delete_api():
    c = _client()
    for attr in ("scan", "scan_iter", "delete_pattern", "cache_del_pattern"):
        assert not hasattr(c, attr), f"Control client must not expose {attr}"
