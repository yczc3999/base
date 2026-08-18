"""
系统健康检查 Handler（不注册路由）

从旧 `main.py` 迁移：`health` / `health_live` / `health_ready`。
路由由 `app.routes.system.register_system_routes()` 注册。
"""

from __future__ import annotations

from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.services.database import get_db
from app.services.redis import get_redis


async def health():
    return {"status": "ok"}


async def health_live():
    return {"status": "ok"}


async def health_ready():
    db_ok, redis_ok = True, True
    try:
        async for session in get_db():
            await session.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        db_ok = False
    try:
        r = await get_redis()
        await r.ping()
    except Exception:  # noqa: BLE001
        redis_ok = False
    if db_ok and redis_ok:
        return {"status": "ready"}
    return JSONResponse(
        {"status": "unready", "db": db_ok, "redis": redis_ok}, status_code=503
    )