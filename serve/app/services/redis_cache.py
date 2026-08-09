"""
Cache Redis — 可丢 versioned 热点缓存客户端（V2 交易域）。

- versioned key：每个键 = namespace:version:name，version 显式必填。
- canonical JSON：sort_keys + 紧凑分隔符 + ensure_ascii=False；禁用 default，
  非 JSON 可序列化对象直接抛 TypeError（程序错误）。
- 强制有限 TTL：无 TTL 用配置默认值；ttl<=0 抛 ValueError（永久 TTL 禁止）。
  TTL 附加 [0, jitter) 均匀抖动，避免惊群。
- pipeline / CAS 支持。
- 故障语义为降级：连接类错误（RedisError/OSError）在 bypass_on_error=True 时
  被吞掉（get→None、写→False），调用方回源即可，不得改变业务判断。
  程序错误（非法 TTL、非可序列化）始终抛出。

禁止：永久 TTL、json.dumps(default=str)、SCAN/pattern delete、
保存 secret / 订单 / 账本 / 资金 / 权限 / 策略权威状态；
Redis ACK 不视为业务完成。
"""

from __future__ import annotations

import json
import random
import time
from typing import Any, NamedTuple, Sequence

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from app.config import CacheRedisEndpoint
from app.services.redis_keys import build_redis_key

_CAS = """
-- KEYS[1] = key  ARGV[1]=expected  ARGV[2]=new  ARGV[3]=ttl_s
local cur = redis.call('GET', KEYS[1])
if cur == ARGV[1] then
    redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
    return 1
end
return 0
"""


def canonical_json(value: Any) -> str:
    """
    canonical JSON 序列化：排序键 + 紧凑分隔符。

    刻意不传 default=——非 JSON 可序列化对象（set/datetime/自定义类）直接抛
    TypeError，保证缓存内容始终是规范 JSON。
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def effective_ttl(base_ttl_s: int, jitter_s: int) -> int:
    """
    有限 TTL + [0, jitter) 均匀抖动（半开区间，不含上界）。

    - jitter=0 → 精确返回 base；
    - base<=0 或 jitter<0 抛 ValueError。
    """
    if base_ttl_s <= 0:
        raise ValueError(f"base TTL must be > 0, got {base_ttl_s}")
    if jitter_s < 0:
        raise ValueError(f"jitter must be >= 0, got {jitter_s}")
    if jitter_s == 0:
        return base_ttl_s
    return base_ttl_s + random.randrange(jitter_s)


class BatchSet(NamedTuple):
    """typed batch SET 操作（frozen）。"""

    name: str
    value: Any
    version: str
    ttl_s: int | None = None


class BatchDelete(NamedTuple):
    """typed batch DELETE 操作（frozen）。"""

    name: str
    version: str


class CacheRedisClient:
    """Cache Redis 客户端：独立连接池 + 独立 namespace，故障降级。"""

    def __init__(self, endpoint: CacheRedisEndpoint) -> None:
        self._endpoint = endpoint
        self._namespace = endpoint.namespace.rstrip(":")
        self._default_ttl = endpoint.default_ttl_s
        self._jitter = endpoint.ttl_jitter_s
        self._bypass = endpoint.bypass_on_error
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

    def _key(self, version: str, name: str) -> str:
        return build_redis_key(self._namespace, version, name)

    def _ttl_or_default(self, ttl_s: int | None) -> int:
        base = self._default_ttl if ttl_s is None else ttl_s
        if base <= 0:
            raise ValueError(
                f"permanent TTL is forbidden; ttl must be > 0, got {base}"
            )
        return effective_ttl(base, self._jitter)

    async def aclose(self) -> None:
        """关闭全部连接；幂等。"""
        await self._client.aclose()

    # ---- health（fail-safe：连接错误只上报不抛，不泄 URL/密码）----

    async def ping(self) -> bool:
        """PING；连接类错误抛给调用方按需降级。"""
        return bool(await self._client.ping())

    async def health(self) -> dict[str, Any]:
        """fail-safe 健康：成功 `{ok:true, latency_ms:<ms>}`；连接类错误
        `{ok:false, latency_ms:null}`。只返回低风险字段，不泄 URL/密码/namespace。"""
        t0 = time.monotonic()
        try:
            await self._client.ping()
            return {
                "ok": True,
                "latency_ms": round((time.monotonic() - t0) * 1000, 3),
            }
        except (RedisError, OSError):
            return {"ok": False, "latency_ms": None}

    # ---- 读写 ----

    async def get(self, name: str, version: str) -> Any | None:
        """读取并反序列化；miss / 连接故障(bypass) 返回 None。"""
        try:
            raw = await self._client.get(self._key(version, name))
        except (RedisError, OSError):
            if self._bypass:
                return None
            raise
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            # 损坏内容视为 miss（可丢缓存，TTL 会清理），不改变业务判断
            return None

    async def set(self, name: str, version: str, value: Any, *, ttl_s: int | None = None) -> bool:
        """写入（有限 TTL + jitter）；连接故障(bypass) 返回 False。"""
        payload = canonical_json(value)   # TypeError 传播（程序错误）
        ttl = self._ttl_or_default(ttl_s)  # ValueError 传播（永久 TTL 禁止）
        try:
            return bool(await self._client.set(self._key(version, name), payload, ex=ttl))
        except (RedisError, OSError):
            if self._bypass:
                return False
            raise

    async def delete(self, name: str, version: str) -> bool:
        """删除精确 key；连接故障(bypass) 返回 False。"""
        try:
            return bool(await self._client.delete(self._key(version, name)))
        except (RedisError, OSError):
            if self._bypass:
                return False
            raise

    # ---- 受控批量操作 / CAS ----

    async def execute_batch(self, ops: Sequence[BatchSet | BatchDelete]) -> list[bool]:
        """
        执行 typed 批量缓存操作（SET/DELETE），返回与 ops 等长的结果列表。

        - 每个 SET 在加入内部 pipeline 前完成 canonical JSON、versioned key、
          有限 TTL+jitter 校验；程序错误（TypeError/ValueError）立即抛出，
          不产生部分执行；
        - 调用方无法取得底层 pipeline/client（禁止绕过 version/TTL/降级）；
        - 连接故障按 bypass：返回 [False]*len(ops)；bypass=False 时抛 RedisError。
        """
        prepared: list[tuple[str, str, Any]] = []  # (cmd, key, payload_or_none)
        for op in ops:
            if isinstance(op, BatchSet):
                payload = canonical_json(op.value)      # TypeError 传播
                ttl = self._ttl_or_default(op.ttl_s)    # ValueError 传播
                prepared.append(("set", self._key(op.version, op.name), (payload, ttl)))
            elif isinstance(op, BatchDelete):
                prepared.append(("delete", self._key(op.version, op.name), None))
            else:
                raise TypeError(f"unsupported batch op: {op!r}")

        try:
            pipe = self._client.pipeline()
            for cmd, key, extra in prepared:
                if cmd == "set":
                    payload, ttl = extra
                    pipe.set(key, payload, ex=ttl)
                else:
                    pipe.delete(key)
            results = await pipe.execute()
        except (RedisError, OSError):
            if self._bypass:
                return [False] * len(prepared)
            raise
        return [bool(r) for r in results]

    async def cas(self, name: str, version: str, expected: Any, new: Any,
                  *, ttl_s: int | None = None) -> bool:
        """原子 CAS：expected 以 canonical JSON 比较；成功 True，冲突 False。"""
        exp_payload = canonical_json(expected)   # TypeError 传播
        new_payload = canonical_json(new)         # TypeError 传播
        ttl = self._ttl_or_default(ttl_s)         # ValueError 传播
        try:
            res = await self._client.eval(
                _CAS, 1, self._key(version, name), exp_payload, new_payload, ttl
            )
            return res == 1
        except (RedisError, OSError):
            if self._bypass:
                return False
            raise
