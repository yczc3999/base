"""
B4 任务/队列监控 — BaseTask last_run 记录 + TaskMonitorLogic

覆盖:
  1. BaseTask.execute 成功/失败记录 last_run (Redis)
  2. list_tasks 返回任务元信息 + 合并 last_run
  3. trigger 走 Queue.push 推 run_task job
  4. queue_status 读各队列长度
"""

import json
import pytest

from app.config import settings
from app.tasks.base import BaseTask
from app.logics.task_monitor import task_monitor_logic

PREFIX = settings.APP_NAME


# ==================== BaseTask last_run ====================

class _DemoTask(BaseTask):
    name = "demo"
    interval = 60

    async def run(self):
        pass


class _FailTask(BaseTask):
    name = "fail"
    interval = 60

    async def run(self):
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_execute_records_last_run_ok(mock_redis_cache):
    t = _DemoTask()
    await t.execute()
    raw = await mock_redis_cache.get(f"{PREFIX}:task:last_run:_DemoTask")
    assert raw is not None
    data = json.loads(raw)
    assert data["status"] == "ok"
    assert data["duration"] >= 0
    assert data["error"] is None


@pytest.mark.asyncio
async def test_execute_records_last_run_failed(mock_redis_cache):
    t = _FailTask()
    await t.execute()  # 不抛异常即通过
    raw = await mock_redis_cache.get(f"{PREFIX}:task:last_run:_FailTask")
    assert raw is not None
    data = json.loads(raw)
    assert data["status"] == "failed"
    assert data["error"] == "boom"


@pytest.mark.asyncio
async def test_lock_skip_does_not_record(mock_redis_cache):
    """锁被占用时 execute 跳过, 不写 last_run"""
    from app.services.redis import get_redis
    r = await get_redis()
    await r.set(f"{PREFIX}:task:lock:_DemoTask", "holder", ex=120, nx=True)

    t = _DemoTask()
    await t.execute()
    raw = await mock_redis_cache.get(f"{PREFIX}:task:last_run:_DemoTask")
    assert raw is None


# ==================== list_tasks ====================

@pytest.mark.asyncio
async def test_list_tasks_returns_metadata(mock_redis_cache):
    tasks = await task_monitor_logic.list_tasks()
    assert isinstance(tasks, list)
    assert tasks, "至少扫描到任务"
    for t in tasks:
        assert "name" in t
        assert "class_name" in t
        assert "interval" in t
        assert "enabled" in t
        assert "last_run" in t  # 未执行过为 None
    # 系统监控任务应在列表里
    class_names = {t["class_name"] for t in tasks}
    assert "SystemMonitorTask" in class_names


@pytest.mark.asyncio
async def test_list_tasks_merges_last_run(mock_redis):
    # 手动写一个真实任务的 last_run, 验证 list_tasks 合并读取
    await mock_redis.set(
        f"{PREFIX}:task:last_run:SystemMonitorTask",
        json.dumps({"status": "ok", "time": 1234567890, "duration": 0.5, "error": None}),
    )
    tasks = await task_monitor_logic.list_tasks()
    smt = next(x for x in tasks if x["class_name"] == "SystemMonitorTask")
    assert smt["last_run"]["status"] == "ok"
    assert smt["last_run"]["duration"] == 0.5


# ==================== trigger ====================

@pytest.mark.asyncio
async def test_trigger_pushes_run_task_job(mock_redis):
    name = await task_monitor_logic.trigger("SystemMonitorTask")
    assert name  # 返回任务显示名
    # 队列 default 应有一条 run_task 消息
    assert await mock_redis.llen(f"{PREFIX}:queue:default") == 1
    raw = (await mock_redis.lrange(f"{PREFIX}:queue:default", 0, -1))[0]
    payload = json.loads(raw)
    assert payload["job"] == "run_task"
    assert payload["data"]["task"] == "SystemMonitorTask"


@pytest.mark.asyncio
async def test_trigger_unknown_task_rejected(mock_redis):
    from app.logics.base import BizError
    with pytest.raises(BizError):
        await task_monitor_logic.trigger("NoSuchTask")


# ==================== queue_status ====================

@pytest.mark.asyncio
async def test_queue_status(mock_redis):
    from app.queue import Queue
    # 造数据: default 2 条, notify 1 条, delayed 1 条
    await Queue.push("job_a")
    await Queue.push("job_b")
    await Queue.push("job_c", queue="notify")
    await Queue.push("job_d", delay=100)

    data = await task_monitor_logic.queue_status()
    assert data["default"] == 2
    assert data["notify"] == 1
    assert data["delayed"] == 1
    assert data["processing"] == 0
    assert data["dead"] == 0
