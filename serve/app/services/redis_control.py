"""
Control Redis — fail-closed 基础设施客户端（V2 交易域）。

只实现基础设施原语，不含业务语义：
- Redis Streams 基础操作（xadd / xread / xlen / xtrim）
- lease acquire / renew / release（Lua 原子，带 owner + fencing token）
- 单调递增 fencing token（INCR 计数器）
- 原子 CAS（Lua）
- health / status

故障语义为 fail-closed：连接故障时所有操作抛 redis RedisError，
调用方必须据此禁止新的增仓工作（performance 设计 §11）。health() 例外：
只上报 ok=False，不抛错，供监控使用。

禁止：业务事实、可淘汰 cache、把 Redis ACK 当业务完成。
"""

from __future__ import annotations

import time
from typing import Any

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from app.config import ControlRedisEndpoint, settings

# ---- Lua 脚本（Redis 服务端原子执行）----

_LEASE_ACQUIRE = """
-- KEYS[1] = lease_key  KEYS[2] = fence_counter_key
-- ARGV[1] = owner  ARGV[2] = ttl_ms
if redis.call('EXISTS', KEYS[1]) == 1 then
    return 0
end
local fence = redis.call('INCR', KEYS[2])
redis.call('SET', KEYS[1], ARGV[1] .. ':' .. fence, 'PX', ARGV[2])
return fence
"""

_LEASE_RENEW = """
-- KEYS[1] = lease_key  ARGV[1]=owner  ARGV[2]=token  ARGV[3]=ttl_ms
local cur = redis.call('GET', KEYS[1])
if not cur then
    return -1
end
local owner, token = string.match(cur, '^(.-):(%d+)$')
if owner ~= ARGV[1] or token ~= ARGV[2] then
    return 0
end
redis.call('PEXPIRE', KEYS[1], ARGV[3])
return 1
"""

_LEASE_RELEASE = """
-- KEYS[1] = lease_key  ARGV[1]=owner  ARGV[2]=token
local cur = redis.call('GET', KEYS[1])
if not cur then
    return -1
end
local owner, token = string.match(cur, '^(.-):(%d+)$')
if owner ~= ARGV[1] or token ~= ARGV[2] then
    return 0
end
redis.call('DEL', KEYS[1])
return 1
"""

_CAS = """
-- KEYS[1] = cas_key  ARGV[1]=expected（空串哨兵=期望"不存在"）  ARGV[2]=new
-- ARGV[3]=ttl_s(<=0 则不过期)
local cur = redis.call('GET', KEYS[1])
local missing = (cur == false)
local expected_missing = (ARGV[1] == '')
if (missing and expected_missing)
    or (not missing and not expected_missing and cur == ARGV[1]) then
    if tonumber(ARGV[3]) > 0 then
        redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
    else
        redis.call('SET', KEYS[1], ARGV[2])
    end
    return 1
end
return 0
"""


class LeaseHandle:
    """成功取得的 lease 句柄；renew/release 必须携带原 owner + token。"""

    __slots__ = ("lease_name", "owner", "token", "ttl_s")

    def __init__(self, lease_name: str, owner: str, token: int, ttl_s: float) -> None:
        self.lease_name = lease_name
        self.owner = owner
        self.token = token
        self.ttl_s = ttl_s

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"LeaseHandle(name={self.lease_name!r}, owner={self.owner!r}, "
            f"token={self.token}, ttl_s={self.ttl_s})"
        )


class ControlRedisClient:
    """Control Redis 客户端：独立连接池 + 独立 namespace，fail-closed。"""

    def __init__(self, endpoint: ControlRedisEndpoint) -> None:
        self._endpoint = endpoint
        self._namespace = endpoint.namespace.rstrip(":")
        self._client = aioredis.Redis.from_url(
            endpoint.url,
            max_connections=endpoint.max_connections,
            socket_connect_timeout=endpoint.connect_timeout_s,
            socket_timeout=endpoint.read_timeout_s,
            health_check_interval=endpoint.health_check_interval_s,
            decode_responses=True,
        )

    # ---- 基础 ----

    @property
    def namespace(self) -> str:
        return self._namespace

    @property
    def pool(self) -> Any:
        """底层 redis-py 连接池（测试用：断言两角色池隔离）。"""
        return self._client.connection_pool

    def _key(self, *parts: str) -> str:
        return f"{self._namespace}:{':'.join(parts)}"

    async def aclose(self) -> None:
        """关闭全部连接；幂等。"""
        await self._client.aclose()

    # ---- health / status（fail-closed 例外：只上报不抛）----

    async def ping(self) -> bool:
        return bool(await self._client.ping())

    async def health(self) -> dict[str, Any]:
        t0 = time.monotonic()
        try:
            await self._client.ping()
            return {
                "ok": True,
                "latency_ms": round((time.monotonic() - t0) * 1000, 3),
                "namespace": self._namespace,
            }
        except RedisError:
            return {"ok": False, "latency_ms": None, "namespace": self._namespace}

    # ---- lease ----

    async def acquire_lease(self, name: str, owner: str, ttl_s: float) -> LeaseHandle | None:
        """原子获取 lease；已被持有返回 None，成功返回带单调递增 fencing token 的句柄。"""
        if ttl_s <= 0:
            raise ValueError(f"ttl_s must be > 0, got {ttl_s}")
        ttl_ms = int(ttl_s * 1000)
        fence = await self._client.eval(
            _LEASE_ACQUIRE, 2,
            self._key("lease", name), self._key("fence", name),
            owner, ttl_ms,
        )
        if fence == 0:
            return None
        return LeaseHandle(name, owner, int(fence), ttl_s)

    async def renew_lease(self, handle: LeaseHandle, ttl_s: float) -> bool:
        """原子续租。非 owner / token 不匹配 / 已过期 → False；成功 → True。"""
        if ttl_s <= 0:
            raise ValueError(f"ttl_s must be > 0, got {ttl_s}")
        ttl_ms = int(ttl_s * 1000)
        res = await self._client.eval(
            _LEASE_RENEW, 1,
            self._key("lease", handle.lease_name),
            handle.owner, handle.token, ttl_ms,
        )
        return res == 1

    async def release_lease(self, handle: LeaseHandle) -> bool:
        """原子释放。非 owner / token 不匹配 / 已过期 → False；成功 → True。"""
        res = await self._client.eval(
            _LEASE_RELEASE, 1,
            self._key("lease", handle.lease_name),
            handle.owner, handle.token,
        )
        return res == 1

    async def fencing_token(self, name: str) -> int | None:
        """当前 lease 名的 fencing 计数器值；从未取得过 → None。"""
        raw = await self._client.get(self._key("fence", name))
        return int(raw) if raw is not None else None

    # ---- 原子 CAS ----

    async def compare_and_swap(self, name: str, expected: str | None, new: str, *,
                               ttl_s: int = 0) -> bool:
        """
        原子 CAS；expected=None 表示期望键"不存在"（首次创建）。
        ttl_s<=0 表示不设过期（由调用方决定）。
        """
        if ttl_s < 0:
            raise ValueError(f"ttl_s must be >= 0, got {ttl_s}")
        exp = "" if expected is None else expected
        res = await self._client.eval(
            _CAS, 1, self._key("cas", name), exp, new, int(ttl_s)
        )
        return res == 1

    # ---- Redis Streams 基础操作 ----

    async def stream_add(self, name: str, fields: dict[str, Any], *, maxlen: int | None = None,
                         approximate: bool = False) -> str:
        """追加一条 stream 消息，返回 message id。"""
        key = self._key("stream", name)
        if maxlen is not None:
            return await self._client.xadd(key, fields, maxlen=maxlen, approximate=approximate)
        return await self._client.xadd(key, fields)

    async def stream_read(self, name: str, *, last_id: str = "0", count: int | None = None,
                          block_ms: int | None = None) -> list[tuple[str, dict[str, Any]]]:
        """从 last_id 读取消息，返回 [(id, fields), ...]；无消息返回空列表。"""
        res = await self._client.xread(
            {self._key("stream", name): last_id}, count=count, block=block_ms
        )
        if not res:
            return []
        entries: list[tuple[str, dict[str, Any]]] = []
        for _stream_name, items in res:
            entries.extend(items)
        return entries

    async def stream_len(self, name: str) -> int:
        return await self._client.xlen(self._key("stream", name))

    async def stream_trim(self, name: str, maxlen: int, approximate: bool = False) -> int:
        """裁剪到 maxlen；返回删除条数。"""
        return await self._client.xtrim(self._key("stream", name), maxlen=maxlen, approximate=approximate)


# 模块级默认实例（WP-00d 前不接入 main.py，仅供显式使用）
control_redis = ControlRedisClient(settings.control_redis_endpoint)
