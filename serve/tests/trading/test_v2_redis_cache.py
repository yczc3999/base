"""
WP-00b Cache Redis 验收测试。

使用本机真实 Redis（需运行中）做集成测试；每个测试用随机 namespace，
结束后只删除自己创建的精确 key。
覆盖：get/set/delete 往返、versioned key、canonical JSON、TTL 必填 + jitter 边界、
非 JSON 可序列化拒绝、namespace/db 隔离、CAS、bypass 降级、
close 幂等、无 SCAN/pattern delete API、pool 隔离。

每个测试用一个 asyncio.run 包住 创建→使用→关闭 完整流程，连接绑定单一临时
loop，避免跨 loop 关闭崩溃，也不依赖 pytest-asyncio 的 loop 生命周期。
"""

import asyncio
import datetime
from uuid import uuid4

import pytest
from redis.exceptions import RedisError

from app.config import CacheRedisEndpoint, ControlRedisEndpoint
from app.services.redis_cache import CacheRedisClient, canonical_json, effective_ttl
from app.services.redis_control import ControlRedisClient

HOST = "localhost"
PORT = 6379


def _cache_endpoint(url: str | None = None, *, namespace: str | None = None,
                    ttl: int = 300, jitter: int = 30, bypass: bool = True) -> CacheRedisEndpoint:
    return CacheRedisEndpoint(
        url=url or f"redis://{HOST}:{PORT}/1",
        max_connections=5,
        connect_timeout_s=2.0,
        read_timeout_s=2.0,
        health_check_interval_s=30.0,
        namespace=namespace or f"pm:it:{uuid4().hex}:cache",
        default_ttl_s=ttl,
        ttl_jitter_s=jitter,
        bypass_on_error=bypass,
    )


def _client(endpoint: CacheRedisEndpoint | None = None) -> CacheRedisClient:
    return CacheRedisClient(endpoint or _cache_endpoint())


async def _cleanup(c: CacheRedisClient, keys: list[str]) -> None:
    if keys:
        await c._client.delete(*keys)


def _run(coro):
    return asyncio.run(coro)


# ---------------- 基础读写 / canonical JSON ----------------

def test_get_set_roundtrip():
    async def _t():
        c = _client()
        name, version = "k1", "v1"
        key = f"{c.namespace}:{version}:{name}"
        try:
            assert await c.set(name, version, {"a": 1, "b": [1, 2]}) is True
            assert await c.get(name, version) == {"a": 1, "b": [1, 2]}
            assert await c.get(name, "other_version") is None  # versioned key
            assert await c.delete(name, version) is True
            assert await c.get(name, version) is None
        finally:
            await _cleanup(c, [key])
            await c.aclose()

    _run(_t())


def test_canonical_json_stable():
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})
    assert " " not in canonical_json({"a": 1})


# ---------------- TTL 必填 + jitter ----------------

def test_set_forces_finite_ttl():
    async def _t():
        c = _client()
        name, version = "ttl1", "v1"
        key = f"{c.namespace}:{version}:{name}"
        try:
            assert await c.set(name, version, {"x": 1}) is True
            pttl = await c._client.pttl(key)
            assert 299_000 <= pttl <= 330_000  # 默认 300s + jitter 0..30
            assert await c.set(name, version, {"x": 1}, ttl_s=5) is True
            pttl2 = await c._client.pttl(key)
            assert 4_500 <= pttl2 <= 35_000    # 显式 5s + jitter 0..30
        finally:
            await _cleanup(c, [key])
            await c.aclose()

    _run(_t())


def test_permanent_ttl_forbidden():
    async def _t():
        c = _client()
        with pytest.raises(ValueError, match="permanent TTL"):
            await c.set("k", "v", {"x": 1}, ttl_s=0)
        with pytest.raises(ValueError, match="permanent TTL"):
            await c.set("k", "v", {"x": 1}, ttl_s=-5)

    _run(_t())


def test_effective_ttl_bounds_and_validation():
    for _ in range(200):
        t = effective_ttl(100, 30)
        assert 100 <= t <= 130
    with pytest.raises(ValueError):
        effective_ttl(0, 30)
    with pytest.raises(ValueError):
        effective_ttl(100, -1)
    assert effective_ttl(60, 0) == 60


# ---------------- 非 JSON 可序列化拒绝 ----------------

def test_non_serializable_rejected():
    async def _t():
        c = _client()
        with pytest.raises(TypeError):
            await c.set("k", "v", {1, 2, 3})                   # set
        with pytest.raises(TypeError):
            await c.set("k", "v", datetime.datetime.now())     # datetime
        with pytest.raises(TypeError):
            await c.set("k", "v", {"a": object()})             # 自定义对象
        assert await c.get("k", "v") is None  # TypeError 在写之前抛出，无残留

    _run(_t())


# ---------------- namespace / db 隔离 ----------------

def test_namespace_isolation_same_db():
    async def _t():
        ns_a = f"pm:it:{uuid4().hex}:cache"
        ns_b = f"pm:it:{uuid4().hex}:cache"
        a = _client(_cache_endpoint(url=f"redis://{HOST}:{PORT}/3", namespace=ns_a))
        b = _client(_cache_endpoint(url=f"redis://{HOST}:{PORT}/3", namespace=ns_b))
        key = f"{ns_a}:v1:shared"
        try:
            assert await a.set("shared", "v1", 42) is True
            assert await a.get("shared", "v1") == 42
            assert await b.get("shared", "v1") is None  # namespace 隔离
        finally:
            await _cleanup(a, [key])
            await a.aclose()
            await b.aclose()

    _run(_t())


def test_db_isolation_same_namespace():
    async def _t():
        ns = f"pm:it:{uuid4().hex}:cache"
        c0 = _client(_cache_endpoint(url=f"redis://{HOST}:{PORT}/4", namespace=ns))
        c1 = _client(_cache_endpoint(url=f"redis://{HOST}:{PORT}/5", namespace=ns))
        key = f"{ns}:v1:shared"
        try:
            assert await c0.set("shared", "v1", 7) is True
            assert await c0.get("shared", "v1") == 7
            assert await c1.get("shared", "v1") is None  # DB 隔离
        finally:
            await _cleanup(c0, [key])
            await c0.aclose()
            await c1.aclose()

    _run(_t())


# ---------------- pipeline / CAS ----------------

def test_pipeline_available():
    c = _client()
    assert c.pipeline() is not None


def test_cas_success_and_conflict():
    async def _t():
        c = _client()
        name, version = "cas", "v1"
        key = f"{c.namespace}:{version}:{name}"
        try:
            assert await c.set(name, version, {"n": 1}) is True
            assert await c.cas(name, version, {"n": 1}, {"n": 2}) is True
            assert await c.get(name, version) == {"n": 2}
            assert await c.cas(name, version, {"n": 1}, {"n": 3}) is False
            assert await c.get(name, version) == {"n": 2}
        finally:
            await _cleanup(c, [key])
            await c.aclose()

    _run(_t())


# ---------------- 故障降级（bypass） ----------------

def test_cache_bypass_on_error():
    async def _t():
        bad = _client(_cache_endpoint(url="redis://127.0.0.1:1/1", bypass=True))
        try:
            assert await bad.get("k", "v") is None              # 读降级为 miss
            assert await bad.set("k", "v", {"x": 1}) is False   # 写降级为失败
            assert await bad.delete("k", "v") is False
            assert await bad.cas("k", "v", 1, 2) is False
        finally:
            await bad.aclose()

    _run(_t())


def test_cache_no_bypass_raises():
    async def _t():
        strict = _client(_cache_endpoint(url="redis://127.0.0.1:1/1", bypass=False))
        try:
            with pytest.raises(RedisError):
                await strict.get("k", "v")
            with pytest.raises(RedisError):
                await strict.set("k", "v", {"x": 1})
        finally:
            await strict.aclose()

    _run(_t())


# ---------------- close 幂等 / 无 SCAN API / pool 隔离 ----------------

def test_aclose_idempotent():
    async def _t():
        c = _client()
        await c.aclose()
        await c.aclose()

    _run(_t())


def test_no_scan_or_pattern_delete_api():
    c = _client()
    for attr in ("scan", "scan_iter", "delete_pattern", "cache_del_pattern"):
        assert not hasattr(c, attr), f"Cache client must not expose {attr}"


def test_cache_and_control_pools_isolated():
    async def _t():
        cache = _client()
        control = ControlRedisClient(
            ControlRedisEndpoint(
                url=f"redis://{HOST}:{PORT}/0",
                max_connections=5, connect_timeout_s=2.0, read_timeout_s=2.0,
                health_check_interval_s=30.0,
                namespace=f"pm:it:{uuid4().hex}:control",
            )
        )
        try:
            assert cache.pool is not control.pool
            assert cache.namespace != control.namespace
        finally:
            await cache.aclose()
            await control.aclose()

    _run(_t())
