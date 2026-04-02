"""
简易速率限制（基于 Redis）

用法：
    from app.utils.rate_limit import check_rate_limit

    if not await check_rate_limit(f"login:{ip}", max_attempts=10, window=300):
        return fail("请求过于频繁，请稍后再试", 429)
"""

from app.services.redis import get_redis


async def check_rate_limit(key: str, max_attempts: int = 10, window: int = 300) -> bool:
    """
    检查速率限制

    :param key:          限制键（如 "login:192.168.1.1"）
    :param max_attempts: 窗口期内最大次数
    :param window:       窗口期（秒）
    :return:             True=允许, False=超限
    """
    r = await get_redis()
    full_key = f"rate_limit:{key}"
    count = await r.incr(full_key)
    if count == 1:
        await r.expire(full_key, window)
    return count <= max_attempts
