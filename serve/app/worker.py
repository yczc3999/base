"""
队列 + 定时任务 Worker

启动：
    python -m app.worker              # 生产
    python -m app.worker --reload     # 开发（热重载）
"""

import asyncio
import importlib
import inspect
import json
import logging
import signal
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("worker")

# 最大并发任务数（防止 OOM）
MAX_CONCURRENT = 10
shutdown_event = asyncio.Event()


# ==================== 扫描 ====================

def scan_tasks():
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


def find_task_by_class(class_name: str):
    """按类名查任务实例（仅启用的任务；不存在返回 None）"""
    for t in scan_tasks():
        if t.__class__.__name__ == class_name:
            return t
    return None


def scan_jobs():
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
            return
        for task in self.tasks:
            asyncio.create_task(self._loop(task))
        logger.info(f"TaskScheduler started ({len(self.tasks)} tasks)")

    async def _loop(self, task):
        await asyncio.sleep(5)
        while not shutdown_event.is_set():
            try:
                await task.execute()
            except Exception as e:
                logger.error(f"Task {task.name} error: {e}")
            # 用 wait 代替 sleep，支持优雅关闭
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=task.interval)
                break  # 收到关闭信号
            except asyncio.TimeoutError:
                pass  # 正常超时，继续循环


# ==================== 队列消费者 ====================

class QueueConsumer:
    def __init__(self, jobs):
        self.jobs = jobs
        self.semaphore = asyncio.Semaphore(MAX_CONCURRENT)
        self.running_tasks = set()

    async def start(self):
        from app.services.redis import get_redis
        from app.config import settings

        r = await get_redis()
        prefix = settings.APP_NAME
        queue_key = f"{prefix}:queue:default"
        processing_key = f"{prefix}:queue:processing"

        logger.info(f"QueueConsumer started (max_concurrent={MAX_CONCURRENT})")

        # 启动时回捞 processing 残留消息（孤儿恢复）
        orphan_count = 0
        while await r.lmove(processing_key, queue_key, "LEFT", "RIGHT"):
            orphan_count += 1
        if orphan_count:
            logger.info(f"Recovered {orphan_count} orphaned messages from processing queue")

        while not shutdown_event.is_set():
            try:
                # BRPOPLPUSH：取出并移入 processing 队列（原子操作，crash 不丢）
                raw = await r.brpoplpush(queue_key, processing_key, timeout=1)
                if raw:
                    payload = json.loads(raw)
                    # 信号量限流
                    await self.semaphore.acquire()
                    task = asyncio.create_task(self._handle(payload, raw, r, processing_key))
                    self.running_tasks.add(task)
                    task.add_done_callback(self.running_tasks.discard)

                # 检查延迟队列
                await self._check_delayed(r, prefix)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Consumer error: {e}")
                await asyncio.sleep(1)

        # 优雅关闭：等待所有正在执行的任务完成
        if self.running_tasks:
            logger.info(f"Waiting for {len(self.running_tasks)} running jobs to finish...")
            await asyncio.gather(*self.running_tasks, return_exceptions=True)

    async def _handle(self, payload, raw, r, processing_key):
        job_name = payload.get("job")
        data = payload.get("data", {})
        retries = payload.get("retries", 0)

        job = self.jobs.get(job_name)
        if not job:
            logger.warning(f"Unknown job: {job_name}")
            await r.lrem(processing_key, 1, raw)
            self.semaphore.release()
            return

        try:
            logger.info(f"Executing: {job_name}")
            await job.handle(data)
            logger.info(f"Done: {job_name}")
        except Exception as e:
            logger.error(f"Failed: {job_name} — {e}")
            if retries < 3:
                delay = 2 ** retries * 60  # 1min, 2min, 4min
                from app.queue import Queue
                await Queue.push(job_name, data, delay=delay, retries=retries + 1)
                logger.info(f"Requeued {job_name} (retry {retries + 1}/3)")
            else:
                from app.config import settings
                dead_key = f"{settings.APP_NAME}:queue:dead"
                await r.lpush(dead_key, raw)
                logger.warning(f"Pushed {job_name} to dead letter queue ({dead_key})")
        finally:
            # ACK：从 processing 队列移除
            await r.lrem(processing_key, 1, raw)
            self.semaphore.release()

    async def _check_delayed(self, r, prefix):
        delayed_key = f"{prefix}:queue:delayed"
        default_key = f"{prefix}:queue:default"
        now = time.time()
        # 用 pipeline 保证原子性
        pipe = r.pipeline()
        items = await r.zrangebyscore(delayed_key, 0, now, start=0, num=20)
        if items:
            for item in items:
                pipe.zrem(delayed_key, item)
                pipe.lpush(default_key, item)
            await pipe.execute()


# ==================== 入口 ====================

async def main():
    logger.info("=" * 50)
    logger.info("Base Platform Worker starting...")
    logger.info("=" * 50)

    # 信号处理
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: shutdown_event.set())

    logger.info("Scanning tasks...")
    tasks = scan_tasks()
    logger.info("Scanning jobs...")
    jobs = scan_jobs()

    scheduler = TaskScheduler(tasks)
    asyncio.create_task(scheduler.start())

    consumer = QueueConsumer(jobs)
    await consumer.start()

    logger.info("Worker stopped.")


def run():
    import argparse
    parser = argparse.ArgumentParser(description="Base Platform Worker")
    parser.add_argument("--reload", action="store_true", help="Hot reload (dev)")
    args = parser.parse_args()

    if args.reload:
        try:
            import watchfiles
            app_dir = Path(__file__).parent
            logger.info("Hot reload enabled")
            watchfiles.run_process(
                str(app_dir / "tasks"), str(app_dir / "jobs"),
                target=lambda: asyncio.run(main()),
            )
        except ImportError:
            logger.warning("watchfiles not installed, no reload")
            asyncio.run(main())
    else:
        asyncio.run(main())


if __name__ == "__main__":
    run()
