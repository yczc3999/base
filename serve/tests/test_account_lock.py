"""
重试次数门（账号级登录失败锁定）测试.

覆盖:
  1. 失败计数累计
  2. 达到阈值后 check_account_locked 返回 True
  3. 未达阈值不锁定
  4. 成功登录清零
  5. 窗口到期自动解锁（TTL 过期模拟）
"""

import pytest

from app.config import settings
from app.utils.account_lock import (
    check_account_locked, record_login_failure, clear_login_failures,
)

PREFIX = settings.APP_NAME


@pytest.mark.asyncio
async def test_failure_count_accumulates(mock_redis):
    for _ in range(3):
        count = await record_login_failure("user1", window=900)
    assert count == 3
    assert await mock_redis.get(f"{PREFIX}:login_fail:user1") == "3"


@pytest.mark.asyncio
async def test_locked_at_threshold(mock_redis):
    # 连续失败 5 次
    for _ in range(5):
        await record_login_failure("user2", window=900)
    assert await check_account_locked("user2", max_failures=5) is True


@pytest.mark.asyncio
async def test_not_locked_below_threshold(mock_redis):
    await record_login_failure("user3", window=900)
    assert await check_account_locked("user3", max_failures=5) is False


@pytest.mark.asyncio
async def test_clear_on_success(mock_redis):
    for _ in range(5):
        await record_login_failure("user4", window=900)
    assert await check_account_locked("user4", max_failures=5) is True

    await clear_login_failures("user4")
    assert await mock_redis.get(f"{PREFIX}:login_fail:user4") is None
    assert await check_account_locked("user4", max_failures=5) is False


@pytest.mark.asyncio
async def test_lock_expires_after_window(mock_redis):
    """窗口到期(计数 key 过期)后自动解锁"""
    for _ in range(5):
        await record_login_failure("user5", window=900)
    assert await check_account_locked("user5", max_failures=5) is True

    # 模拟 key 过期
    await mock_redis.delete(f"{PREFIX}:login_fail:user5")
    assert await check_account_locked("user5", max_failures=5) is False
