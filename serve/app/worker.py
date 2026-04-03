"""
队列 + 定时任务 Worker

启动：
    python -m app.worker              # 生产
    python -m app.worker --reload     # 开发（热重载）

多 Worker：
    python -m app.worker --workers 4
"""

import asyncio
import importlib
import inspect
import json
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("worker")


# ==================== 扫描 ====================

def scan_tasks():
    """扫描 app/tasks/ 目录，自动发现所有 Task 类"""
    from app.tasks.base import BaseTask

    tasks = []
    task_dir = Path(__file__).parent / "tasks"
    for file in sorted(task_dir.glob("*.py")):
        if file.name.startswith("_") or file.name == "base.py":
            continue
        try:
            module = importlib.import_module(f"app.tasks.{file.stem}")
            for name, cls in inspect.getmembers(module, inspect.isclass):
                if issubclass(cls, BaseTask) and cls is not BaseTask and cls.enabled:
                    tasks.append(cls())
                    logger.info(f"  Task: {cls.name} (interval={cls.interval}s)")
        except Exception as e:
            logger.error(f"  Failed to load task {file.name}: {e}")
    return tasks


def scan_jobs():
    """扫描 app/jobs/ 目录，自动发现所有 Job 类"""
    from app.jobs.base import BaseJob

    jobs = {}
    job_dir = Path(__file__).parent / "jobs"
    for file in sorted(job_dir.glob("*.py")):
        if file.name.startswith("_") or file.name == "base.py":
            continue
        try:
            module = importlib.import_module(f"app.jobs.{file.stem}")
            for name, cls in inspect.getmembers(module, inspect.isclass):
                if issubclass(cls, BaseJob) and cls is not BaseJob and cls.name:
                    jobs[cls.name] = cls()
                    logger.info(f"  Job: {cls.name}")
        except Exception as e:
            logger.error(f"  Failed to load job {file.name}: {e}")
    return jobs


# ==================== 定时调度器 ====================

class TaskScheduler:
    def __init__(self, tasks):
        self.tasks = tasks

    async def start(self):
        if not self.tasks:
            logger.info("No tasks registered")
            return
        for task in self.tasks:
            asyncio.create_task(self._loop(task))
        logger.info(f"TaskScheduler started ({len(self.tasks)} tasks)")

    async def _loop(self, task):
        # 首次启动延迟 5 秒（等 Redis 连接就绪）
        await asyncio.sleep(5)
        while True:
            try:
                await task.execute()
            except Exception as e:
                logger.error(f"Task {task.name} error: {e}")
            await asyncio.sleep(task.interval)


# ==================== 队列消费者 ====================

class QueueConsumer:
    def __init__(self, jobs):
        self.jobs = jobs

    async def start(self):
        from app.services.redis import get_redis
        from app.config import settings

        r = await get_redis()
        prefix = settings.APP_NAME
        queues = [f"{prefix}:queue:default"]

        logger.info(f"QueueConsumer started (listening: {', '.join(queues)})")

        while True:
            try:
                # BRPOP 阻塞等待
                result = await r.brpop(queues, timeout=1)
                if result:
                    _, raw = result
                    payload = json.loads(raw)
                    asyncio.create_task(self._handle(payload))

                # 检查延迟队列
                await self._check_delayed(r, prefix)
            except Exception as e:
                logger.error(f"Consumer error: {e}")
                await asyncio.sleep(1)

    async def _handle(self, payload):
        job_name = payload.get("job")
        data = payload.get("data", {})

        job = self.jobs.get(job_name)
        if not job:
            logger.warning(f"Unknown job: {job_name}")
            return

        try:
            logger.info(f"Executing job: {job_name}")
            await job.handle(data)
            logger.info(f"Job {job_name} done")
        except Exception as e:
            logger.error(f"Job {job_name} failed: {e}")

    async def _check_delayed(self, r, prefix):
        """到期的延迟任务移入默认队列"""
        delayed_key = f"{prefix}:queue:delayed"
        now = time.time()
        items = await r.zrangebyscore(delayed_key, 0, now, start=0, num=10)
        for item in items:
            await r.zrem(delayed_key, item)
            await r.lpush(f"{prefix}:queue:default", item)


# ==================== 入口 ====================

async def main():
    logger.info("=" * 50)
    logger.info("Base Platform Worker starting...")
    logger.info("=" * 50)

    logger.info("Scanning tasks...")
    tasks = scan_tasks()

    logger.info("Scanning jobs...")
    jobs = scan_jobs()

    # 启动调度器
    scheduler = TaskScheduler(tasks)
    asyncio.create_task(scheduler.start())

    # 启动消费者（阻塞）
    consumer = QueueConsumer(jobs)
    await consumer.start()


def run():
    import argparse
    parser = argparse.ArgumentParser(description="Base Platform Worker")
    parser.add_argument("--reload", action="store_true", help="Enable hot reload (dev only)")
    args = parser.parse_args()

    if args.reload:
        try:
            import watchfiles
            logger.info("Hot reload enabled (watching app/tasks/ and app/jobs/)")
            watchfiles.run_process(
                "app/tasks/", "app/jobs/",
                target=lambda: asyncio.run(main()),
            )
        except ImportError:
            logger.warning("watchfiles not installed, running without reload")
            asyncio.run(main())
    else:
        asyncio.run(main())


if __name__ == "__main__":
    run()
