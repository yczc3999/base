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
from app.services.redis_cache import (
    BatchDelete,
    BatchSet,
    CacheRedisClient,
    canonical_json,
    effective_ttl,
)
from app.services.redis_control import ControlRedisClient
from app.services.redis_keys import build_redis_key

HOST = "localhost"
PORT = 6379

# SET→PTTL 之间允许的自然流逝容差（ms）。生成 TTL 上界仍被严格校验，
# 下界只允许这一明确的耗时余量，不依赖"零耗时"假设。
ELAPSED_TOLERANCE_MS = 2000


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
        key = build_redis_key(c.namespace, version, name)
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
        key = build_redis_key(c.namespace, version, name)
        try:
            assert await c.set(name, version, {"x": 1}) is True
            pttl = await c._client.pttl(key)
            # 允许 SET→PTTL 自然流逝（tolerance），但上界不超过生成 TTL
            assert pttl <= 330_000                       # jitter 上界 300+30s
            assert pttl >= 300_000 - ELAPSED_TOLERANCE_MS
            assert await c.set(name, version, {"x": 1}, ttl_s=5) is True
            pttl2 = await c._client.pttl(key)
            assert pttl2 <= 35_000                       # 上界 5+30s
            assert pttl2 >= 5_000 - ELAPSED_TOLERANCE_MS
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
    # 半开区间 [base, base+jitter)：多次运行严格不含上界
    for _ in range(200):
        t = effective_ttl(100, 30)
        assert 100 <= t < 130
    # jitter=0 → 精确 base（且保持半开语义）
    assert effective_ttl(60, 0) == 60
    with pytest.raises(ValueError):
        effective_ttl(0, 30)
    with pytest.raises(ValueError):
        effective_ttl(100, -1)


def test_effective_ttl_half_open_never_hits_upper_bound():
    seen = {effective_ttl(10, 5) for _ in range(2000)}
    assert 10 <= min(seen) < max(seen) < 15
    assert 15 not in seen


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
        key = build_redis_key(ns_a, "v1", "shared")
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
        key = build_redis_key(ns, "v1", "shared")
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

def test_no_raw_pipeline_api():
    c = _client()
    assert not hasattr(c, "pipeline"), "raw pipeline must not be exposed"
    # typed batch 是唯一批量入口
    assert hasattr(c, "execute_batch")
    assert BatchSet._fields == ("name", "value", "version", "ttl_s")
    assert BatchDelete._fields == ("name", "version")
    asyncio.run(c.aclose())


def test_batch_set_delete_order_and_results():
    async def _t():
        c = _client()
        keys = [
            build_redis_key(c.namespace, "v1", "b1"),
            build_redis_key(c.namespace, "v1", "b2"),
        ]
        try:
            res = await c.execute_batch([
                BatchSet("b1", {"a": 1}, "v1"),
                BatchSet("b2", [1, 2], "v1"),
            ])
            assert res == [True, True]
            assert await c.get("b1", "v1") == {"a": 1}
            assert await c.get("b2", "v1") == [1, 2]
            # DELETE 顺序执行
            res_del = await c.execute_batch([BatchDelete("b1", "v1"), BatchDelete("b2", "v1")])
            assert res_del == [True, True]
            assert await c.get("b1", "v1") is None
            assert await c.get("b2", "v1") is None
        finally:
            await _cleanup(c, keys)
            await c.aclose()

    _run(_t())


def test_batch_enforces_finite_ttl_and_versioned_key():
    async def _t():
        c = _client()
        try:
            with pytest.raises(ValueError, match="permanent TTL"):
                await c.execute_batch([BatchSet("x", {"a": 1}, "v1", ttl_s=0)])
            with pytest.raises(TypeError):
                await c.execute_batch([BatchSet("x", {1, 2}, "v1")])   # set 不可序列化
            # 程序错误在写之前抛，无残留
            assert await c.get("x", "v1") is None
            assert await c.get("x", "v2") is None   # versioned：同名不同版本隔离
        finally:
            await c.aclose()

    _run(_t())


def test_batch_bypass_on_error():
    async def _t():
        bad = _client(_cache_endpoint(url="redis://127.0.0.1:1/1", bypass=True))
        try:
            res = await bad.execute_batch([
                BatchSet("a", 1, "v1"),
                BatchDelete("b", "v1"),
            ])
            assert res == [False, False]   # 与操作数量一致的失败结果
        finally:
            await bad.aclose()

    _run(_t())


def test_batch_rejects_unknown_op_type():
    async def _t():
        c = _client()
        with pytest.raises(TypeError):
            await c.execute_batch([("set", "x", "v1")])  # 裸 tuple 非 typed op
        await c.aclose()

    _run(_t())


def test_batch_set_applies_ttl_jitter():
    async def _t():
        c = _client(_cache_endpoint(ttl=100, jitter=10))
        key = build_redis_key(c.namespace, "v1", "ttlb")
        try:
            await c.execute_batch([BatchSet("ttlb", {"x": 1}, "v1")])
            pttl = await c._client.pttl(key)
            # 允许 SET→PTTL 自然流逝；上界不超过生成 TTL（100+jitter 上界 110s）
            assert pttl <= 110_000
            assert pttl >= 100_000 - ELAPSED_TOLERANCE_MS
        finally:
            await _cleanup(c, [key])
            await c.aclose()

    _run(_t())


def test_cas_success_and_conflict():
    async def _t():
        c = _client()
        name, version = "cas", "v1"
        key = build_redis_key(c.namespace, version, name)
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
    asyncio.run(c.aclose())


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


def test_cache_uses_shared_encoder():
    c = _client()
    assert c._key("v1", "k1") == build_redis_key(c.namespace, "v1", "k1")
    # 复现审查用例：version/name 分隔不同 → 不同 key
    assert c._key("a:b", "c") != c._key("a", "b:c")
    asyncio.run(c.aclose())
