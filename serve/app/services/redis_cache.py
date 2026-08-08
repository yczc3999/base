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
from typing import Any

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from app.config import CacheRedisEndpoint

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
    """有限 TTL + [0, jitter) 均匀抖动；base<=0 或 jitter<0 抛 ValueError。"""
    if base_ttl_s <= 0:
        raise ValueError(f"base TTL must be > 0, got {base_ttl_s}")
    if jitter_s < 0:
        raise ValueError(f"jitter must be >= 0, got {jitter_s}")
    return base_ttl_s + random.randint(0, jitter_s)


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
        return f"{self._namespace}:{version}:{name}"

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

    # ---- pipeline / CAS ----

    def pipeline(self) -> Any:
        """redis-py pipeline（调用方负责 execute）。"""
        return self._client.pipeline()

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
