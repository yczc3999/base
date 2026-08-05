"""系统监控（每 60 秒）"""

import os
import time
from app.tasks.base import BaseTask

# 项目根目录（磁盘采集用所在分区）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))


def _memory_metrics() -> dict:
    """内存指标（Linux /proc/meminfo, 非 Linux 返回空）."""
    try:
        with open("/proc/meminfo") as f:
            data = {}
            for line in f:
                key, _, rest = line.partition(":")
                if not rest:
                    continue
                # 单位 kB → bytes
                data[key.strip()] = int(rest.strip().split()[0]) * 1024
        total = data.get("MemTotal", 0)
        available = data.get("MemAvailable", 0)
        if not total:
            return {}
        used = total - available
        return {
            "mem_total": total,
            "mem_available": available,
            "mem_used": used,
            "mem_used_percent": round(used / total * 100, 1),
        }
    except (OSError, ValueError):
        return {}


def _disk_metrics() -> dict:
    """磁盘指标（项目根目录所在分区）."""
    try:
        st = os.statvfs(_PROJECT_ROOT)
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        if not total:
            return {}
        used = total - free
        return {
            "disk_total": total,
            "disk_free": free,
            "disk_used": used,
            "disk_used_percent": round(used / total * 100, 1),
        }
    except (OSError, ValueError):
        return {}


async def _redis_metrics() -> dict:
    """Redis 内存指标."""
    try:
        from app.services.redis import get_redis
        r = await get_redis()
        info = await r.info("memory")
        return {"redis_used_memory": int(info.get("used_memory", 0))}
    except Exception:
        return {}


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
            "ts": time.time(),
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

        # 内存 / 磁盘 / Redis 内存（P1-4）
        metrics["memory"] = _memory_metrics()
        metrics["disk"] = _disk_metrics()
        metrics["redis"] = await _redis_metrics()

        await cache_set("system:metrics", metrics, ttl=120)

        cpu_count = os.cpu_count() or 1
        if load_1 > cpu_count * 0.9:
            import logging
            logging.getLogger("task").warning(f"High CPU load: {load_1} (cores: {cpu_count})")
