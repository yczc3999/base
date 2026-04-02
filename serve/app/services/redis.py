"""
Redis 服务 — 连接管理 + 缓存工具函数

提供统一的 Redis 异步连接管理和常用缓存操作。
所有缓存值自动 JSON 序列化/反序列化。

使用方式：
    from app.services.redis import cache_get, cache_set, cache_del

    await cache_set("user:1", {"name": "张三"}, ttl=3600)
    user = await cache_get("user:1")  # → {"name": "张三"}
    await cache_del("user:1")
"""

import json
from typing import Any
from redis.asyncio import Redis
from app.config import settings

# 单例连接（模块级）
_redis: Redis | None = None


async def get_redis() -> Redis:
    """
    获取 Redis 异步连接（单例）

    首次调用时创建连接，后续调用复用同一连接。
    连接参数从 settings 中读取。
    """
    global _redis
    if _redis is None:
        _redis = Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB,
            password=settings.REDIS_PASSWORD or None,
            decode_responses=True,
        )
    return _redis


async def close_redis():
    """关闭 Redis 连接（在应用关闭时调用）"""
    global _redis
    if _redis:
        await _redis.close()
        _redis = None


# ==================== 缓存操作 ====================


async def cache_get(key: str) -> Any:
    """
    读取缓存

    :param key: 缓存 key
    :return: 反序列化后的值，key 不存在返回 None

    自动 JSON 反序列化：
        - 存的是 dict/list → 返回 dict/list
        - 存的是字符串（非 JSON）→ 返回原始字符串
        - key 不存在 → 返回 None
    """
    r = await get_redis()
    val = await r.get(key)
    if val is None:
        return None
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return val


async def cache_set(key: str, value: Any, ttl: int = 0):
    """
    写入缓存

    :param key:   缓存 key
    :param value: 任意可序列化的值（dict / list / str / int 等）
    :param ttl:   过期时间（秒），0 = 永不过期

    自动 JSON 序列化，datetime 等特殊类型通过 default=str 转换。
    """
    r = await get_redis()
    data = json.dumps(value, ensure_ascii=False, default=str)
    if ttl > 0:
        await r.set(key, data, ex=ttl)
    else:
        await r.set(key, data)


async def cache_del(*keys: str):
    """
    删除缓存

    :param keys: 一个或多个缓存 key

    支持批量删除：cache_del("key1", "key2", "key3")
    """
    if not keys:
        return
    r = await get_redis()
    await r.delete(*keys)


async def cache_del_pattern(pattern: str, max_iterations: int = 10):
    """
    按模式批量删除 key

    :param pattern:        匹配模式（如 "user:*"、"cache:admin:*"）
    :param max_iterations: 最大 SCAN 迭代次数（防止大量 key 时阻塞）

    使用 SCAN 命令遍历匹配的 key 并批量删除。
    每次 SCAN 返回最多 1000 个 key，超过 max_iterations 次迭代后停止。

    示例：
        await cache_del_pattern("admin_user:*")     # 删除所有 admin_user 缓存
        await cache_del_pattern("base:token:*")     # 删除所有 token
    """
    r = await get_redis()
    cursor = 0
    iterations = 0
    while True:
        iterations += 1
        cursor, keys = await r.scan(cursor, match=pattern, count=1000)
        if keys:
            await r.delete(*keys)
        if cursor == 0 or iterations >= max_iterations:
            break


async def cache_scan(pattern: str, limit: int = 100, max_iterations: int = 5) -> list[str]:
    """
    按模式搜索 key（只返回 key 名，不读取值）

    :param pattern:        匹配模式（如 "user:*"）
    :param limit:          最多返回的 key 数量
    :param max_iterations: 最大 SCAN 迭代次数（防止阻塞）
    :return:               匹配的 key 列表

    示例：
        keys = await cache_scan("base:token:*", limit=50)
        # → ["base:token:abc123", "base:token:def456", ...]
    """
    r = await get_redis()
    cursor = 0
    result = []
    iterations = 0
    while True:
        iterations += 1
        cursor, keys = await r.scan(cursor, match=pattern, count=limit)
        if keys:
            result.extend(keys)
        if len(result) >= limit or cursor == 0 or iterations >= max_iterations:
            break
    return result[:limit]
