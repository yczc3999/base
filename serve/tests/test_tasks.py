"""
BaseTask 测试 — 防重复锁 / 错误隔离
"""

import pytest

from app.config import settings


@pytest.mark.asyncio
async def test_lock_prevents_concurrent_execution(mock_redis):
    """顺序调用两次都执行（锁在执行后被释放）"""
    from app.tasks.base import BaseTask
    ran = []

    class TaskA(BaseTask):
        name = "task_a"
        interval = 60

        async def run(self):
            ran.append(1)

    t = TaskA()
    await t.execute()
    await t.execute()
    assert len(ran) == 2


@pytest.mark.asyncio
async def test_lock_blocks_second_holder(mock_redis):
    """锁被他人持有：execute 跳过"""
    from app.tasks.base import BaseTask
    ran = []

    class TaskB(BaseTask):
        name = "task_b"
        interval = 60

        async def run(self):
            ran.append(1)

    key = f"{settings.APP_NAME}:task:lock:TaskB"
    await mock_redis.set(key, "other-holder", ex=120, nx=True)

    t = TaskB()
    await t.execute()
    assert len(ran) == 0


@pytest.mark.asyncio
async def test_lock_owner_token_release(mock_redis):
    """execute 后锁被正确释放"""
    from app.tasks.base import BaseTask

    class TaskC(BaseTask):
        name = "task_c"
        interval = 60

        async def run(self):
            pass

    t = TaskC()
    await t.execute()
    key = f"{settings.APP_NAME}:task:lock:TaskC"
    assert await mock_redis.get(key) is None


@pytest.mark.asyncio
async def test_task_error_does_not_crash(mock_redis):
    """run() 抛异常不影响 execute 返回"""
    from app.tasks.base import BaseTask

    class TaskD(BaseTask):
        name = "task_d"
        interval = 60

        async def run(self):
            raise RuntimeError("boom")

    t = TaskD()
    await t.execute()  # 不抛异常即通过
    # 异常后锁仍被正确释放
    key = f"{settings.APP_NAME}:task:lock:TaskD"
    assert await mock_redis.get(key) is None
