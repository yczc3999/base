from fastapi import APIRouter, Depends
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, date, timedelta
from app.services.database import get_db
from app.utils.response import ok
from app.models import AdminUser, User, AdminOperationLog, AdminLoginLog, Message
from app.deps import AuthInfo, require_admin
from app.services.redis import get_redis
from app.config import settings
import platform
import sys

router = APIRouter()


@router.get("/dashboard/stats")
async def dashboard_stats(auth: AuthInfo = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """仪表盘统计数据"""
    today = date.today()
    month_start = today.replace(day=1)

    # 并行查询
    admin_count = (await db.execute(select(func.count()).select_from(AdminUser))).scalar_one()
    user_count = (await db.execute(select(func.count()).select_from(User))).scalar_one()
    today_logins = (await db.execute(
        select(func.count()).select_from(AdminLoginLog).where(
            and_(AdminLoginLog.status == 1, func.date(AdminLoginLog.created_at) == today)
        )
    )).scalar_one()
    month_operations = (await db.execute(
        select(func.count()).select_from(AdminOperationLog).where(
            AdminOperationLog.created_at >= datetime.combine(month_start, datetime.min.time())
        )
    )).scalar_one()
    unread_messages = (await db.execute(
        select(func.count()).select_from(Message).where(
            and_(Message.user_id == auth.user_id, Message.is_read == False)
        )
    )).scalar_one()

    return ok({
        "admin_count": admin_count,
        "user_count": user_count,
        "total_users": admin_count + user_count,
        "today_logins": today_logins,
        "month_operations": month_operations,
        "unread_messages": unread_messages,
    })


@router.get("/dashboard/system")
async def dashboard_system(auth: AuthInfo = Depends(require_admin)):
    """系统状态信息"""
    import os
    import time

    # Redis 状态
    r = await get_redis()
    redis_info = await r.info("server")
    redis_memory = await r.info("memory")

    # 进程运行时间
    uptime_seconds = int(time.time() - time.monotonic())

    return ok({
        "version": "1.0.0",
        "framework": f"FastAPI + Python {sys.version.split()[0]}",
        "database": "PostgreSQL",
        "cache": "Redis",
        "os": f"{platform.system()} {platform.release()}",
        "python": sys.version.split()[0],
        "cpu_count": os.cpu_count(),
        "redis_version": redis_info.get("redis_version", "—"),
        "redis_memory": redis_memory.get("used_memory_human", "—"),
        "redis_keys": await r.dbsize(),
    })


@router.get("/dashboard/recent")
async def dashboard_recent(auth: AuthInfo = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """最近操作日志"""
    stmt = (
        select(AdminOperationLog)
        .order_by(AdminOperationLog.id.desc())
        .limit(8)
    )
    result = await db.execute(stmt)
    rows = result.scalars().all()

    return ok([
        {
            "id": r.id,
            "username": r.username,
            "action": r.action,
            "module": r.module,
            "method": r.method,
            "ip": r.ip,
            "status_code": r.status_code,
            "duration": r.duration,
            "created_at": r.created_at.isoformat() if r.created_at else "",
        }
        for r in rows
    ])
