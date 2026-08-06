"""账号级登录失败锁定（重试次数门）.

背景: 仅 IP 级 rate_limit 可被换 IP 绕过, 无法防对同一账号的分布式暴力破解。
本模块按账号累计登录失败次数, 达到阈值即锁定（滑动窗口 TTL 到期自动解锁）。

流程:
  登录前  check_account_locked()  → True 则拒绝 (429)
  失败时  record_login_failure()  → 累计计数, 首次设置窗口 TTL
  成功时  clear_login_failures()  → 清零

参数: max_failures(连续失败阈值) / lock_minutes(锁定分钟数), 通过 settings 表
       category=login_security 配置, 默认 5 次 / 15 分钟。
"""
from app.config import settings
from app.services.redis import get_redis

PREFIX = settings.APP_NAME


def _fail_key(username: str) -> str:
    return f"{PREFIX}:login_fail:{username}"


async def check_account_locked(username: str, max_failures: int = 5) -> bool:
    """是否已锁定（失败计数 >= 阈值）."""
    r = await get_redis()
    raw = await r.get(_fail_key(username))
    return raw is not None and int(raw) >= max_failures


async def record_login_failure(username: str, window: int = 900) -> int:
    """记录一次登录失败, 返回当前累计次数（首次调用启动窗口 TTL）."""
    r = await get_redis()
    key = _fail_key(username)
    count = await r.incr(key)
    if count == 1:
        await r.expire(key, window)
    return count


async def clear_login_failures(username: str):
    """登录成功清零失败计数."""
    r = await get_redis()
    await r.delete(_fail_key(username))
