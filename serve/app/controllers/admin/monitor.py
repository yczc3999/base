"""系统监控展示 — 读取 system_monitor 采集的 system:metrics.

只读端点, 权限 admin:monitor:list。
"""
from fastapi import Depends

from app.deps import AuthInfo, current_auth
from app.services.redis import get_redis
from app.utils.response import ok, fail

async def monitor_metrics(auth: AuthInfo = Depends(current_auth)):
    """返回最近一次采集的 system:metrics (CPU/内存/磁盘/Redis/队列)."""
    try:
        r = await get_redis()
        raw = await r
        if not raw:
            return ok({"empty": True, "msg": "暂无数据，等待监控任务采集（每 60s）"})
        import json
        return ok(json.loads(raw))
    except Exception as e:
        return fail(f"读取监控数据失败: {e}")
