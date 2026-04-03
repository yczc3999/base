"""
队列客户端

用法：
    from app.queue import Queue

    # 立即执行
    await Queue.push("send_sms", {"phone": "13800138000", "code": "1234"})

    # 延迟 30 秒
    await Queue.push("send_notify", {"title": "完成"}, delay=30)

    # 指定队列
    await Queue.push("export_data", {"table": "orders"}, queue="export")
"""

import json
import time
from app.services.redis import get_redis
from app.config import settings

PREFIX = settings.APP_NAME


class Queue:
    @staticmethod
    async def push(job_name: str, data: dict = None, delay: int = 0, queue: str = "default"):
        """
        推入队列

        :param job_name: 任务名（对应 jobs/ 下的 BaseJob.name）
        :param data:     任务数据
        :param delay:    延迟秒数（0=立即）
        :param queue:    队列名（default / export / notify / task）
        """
        r = await get_redis()
        payload = json.dumps({
            "job": job_name,
            "data": data or {},
            "created_at": time.time(),
        }, default=str)

        if delay > 0:
            await r.zadd(f"{PREFIX}:queue:delayed", {payload: time.time() + delay})
        else:
            await r.lpush(f"{PREFIX}:queue:{queue}", payload)

    @staticmethod
    async def size(queue: str = "default") -> int:
        """获取队列长度"""
        r = await get_redis()
        return await r.llen(f"{PREFIX}:queue:{queue}")
