"""run_task — 手动触发定时任务.

task_monitor 页面点「立即执行」→ Queue.push("run_task", {"task": class_name})
→ worker 消费 → 按类名找到任务实例 → execute()（正常获取锁, 防并发）。
"""
import logging

from app.jobs.base import BaseJob

logger = logging.getLogger("job")


class RunTaskJob(BaseJob):
    name = "run_task"

    async def handle(self, data: dict):
        task_cls_name = (data or {}).get("task")
        if not task_cls_name:
            raise ValueError("缺少 task 参数")

        from app.worker import find_task_by_class
        task = find_task_by_class(task_cls_name)
        if not task:
            raise ValueError(f"任务不存在或未启用: {task_cls_name}")

        logger.info(f"[run_task] manual trigger: {task.name}")
        await task.execute()
        logger.info(f"[run_task] done: {task.name}")
