"""系统监控（每 60 秒）"""

import os
from app.tasks.base import BaseTask


class SystemMonitorTask(BaseTask):
    name = "系统监控"
    interval = 60

    async def run(self):
        from app.services.redis import cache_set

        # 采集系统指标
        try:
            load_1, load_5, load_15 = os.getloadavg()
        except OSError:
            load_1 = load_5 = load_15 = 0

        metrics = {
            "load_1": round(load_1, 2),
            "load_5": round(load_5, 2),
            "load_15": round(load_15, 2),
            "cpu_count": os.cpu_count(),
        }

        # 存入 Redis（Dashboard 可读取）
        await cache_set("system:metrics", metrics, ttl=120)

        # CPU 负载过高告警
        cpu_count = os.cpu_count() or 1
        if load_1 > cpu_count * 0.9:
            import logging
            logging.getLogger("task").warning(f"High CPU load: {load_1} (cores: {cpu_count})")
            # 可接入 NotifyService 发告警
