"""系统监控（每 60 秒）"""

import os
from app.tasks.base import BaseTask


class SystemMonitorTask(BaseTask):
    name = "系统监控"
    interval = 60

    async def run(self):
        from app.services.redis import cache_set, get_redis
        from app.config import settings

        try:
            load_1, load_5, load_15 = os.getloadavg()
        except (OSError, AttributeError):
            load_1 = load_5 = load_15 = 0

        metrics = {
            "load_1": round(load_1, 2),
            "load_5": round(load_5, 2),
            "load_15": round(load_15, 2),
            "cpu_count": os.cpu_count(),
        }

        # 队列深度采集（P1-4 监控仪表板数据源之一）
        try:
            r = await get_redis()
            prefix = settings.APP_NAME
            queues = {}
            for q in ("default", "export", "notify", "task"):
                queues[q] = await r.llen(f"{prefix}:queue:{q}")
            queues["processing"] = await r.llen(f"{prefix}:queue:processing")
            queues["delayed"] = await r.zcard(f"{prefix}:queue:delayed")
            queues["dead"] = await r.llen(f"{prefix}:queue:dead")
            metrics["queues"] = queues
        except Exception:
            metrics["queues"] = {}  # Redis 故障不影响 CPU 采集

        await cache_set("system:metrics", metrics, ttl=120)

        cpu_count = os.cpu_count() or 1
        if load_1 > cpu_count * 0.9:
            import logging
            logging.getLogger("task").warning(f"High CPU load: {load_1} (cores: {cpu_count})")
