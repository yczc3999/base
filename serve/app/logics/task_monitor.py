"""定时任务 / 队列监控逻辑.

- 任务列表: 复用 worker.scan_tasks() 拿静态元信息 + Redis 读最近执行状态
- 手动触发: 校验任务存在后 Queue.push("run_task", ...) 走队列
- 队列状态: 从 Redis 读 default/export/notify/task/processing/delayed/dead 长度
"""
import json
from app.config import settings
from app.logics.base import BizError
from app.queue import Queue
from app.services.redis import get_redis

PREFIX = settings.APP_NAME

_QUEUE_NAMES = ("default", "export", "notify", "task")


class TaskMonitorLogic:
    async def list_tasks(self) -> list[dict]:
        """扫描任务 + 合并最近执行状态."""
        from app.worker import scan_tasks

        r = await get_redis()
        tasks = []
        for t in scan_tasks():
            class_name = t.__class__.__name__
            raw = await r.get(f"{PREFIX}:task:last_run:{class_name}")
            last_run = json.loads(raw) if raw else None
            tasks.append({
                "name": t.name,
                "class_name": class_name,
                "interval": t.interval,
                "enabled": t.enabled,
                "last_run": last_run,
            })
        return tasks

    async def trigger(self, class_name: str) -> str:
        """手动触发任务（走默认队列, 与定时推入完全一致）. 返回任务显示名."""
        from app.worker import find_task_by_class

        task = find_task_by_class(class_name)
        if not task:
            raise BizError(f"任务不存在或未启用: {class_name}")
        await Queue.push("run_task", {"task": class_name})
        return task.name

    async def queue_status(self) -> dict:
        """各队列长度 + processing 残留 + delayed 延迟数 + dead 死信数."""
        r = await get_redis()
        data = {q: await r.llen(f"{PREFIX}:queue:{q}") for q in _QUEUE_NAMES}
        data["processing"] = await r.llen(f"{PREFIX}:queue:processing")
        data["delayed"] = await r.zcard(f"{PREFIX}:queue:delayed")
        data["dead"] = await r.llen(f"{PREFIX}:queue:dead")
        return data


task_monitor_logic = TaskMonitorLogic()
