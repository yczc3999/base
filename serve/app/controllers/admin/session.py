"""在线会话管理 — 查看活跃 session 列表 + 一键踢下线.

数据源: Redis user_tokens 索引 (SCAN 枚举) + token 详情 (username/scope/ttl)。
"""
import json
import time

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.deps import AuthInfo, current_auth
from app.services.database import get_db
from app.services.redis import get_redis
from app.utils.response import ok, fail
from app.utils.token import revoke_all_tokens

PREFIX = settings.APP_NAME


async def session_list(auth: AuthInfo = Depends(current_auth)):
    """枚举所有在线会话（按 user_tokens 索引 + token 详情）."""
    r = await get_redis()
    pattern = f"{PREFIX}:user_tokens:*"

    sessions = []
    cursor = 0
    while True:
        cursor, keys = await r.scan(cursor, match=pattern, count=200)
        for key in keys:
            # key = {prefix}:user_tokens:{scope}:{user_id}
            parts = key.split(":")
            if len(parts) < 4:
                continue
            scope = parts[2]
            user_id = parts[3]

            tokens = await r.smembers(key)
            for tok in tokens:
                raw = await r.get(f"{PREFIX}:token:{tok}")
                if not raw:
                    continue
                try:
                    info = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue
                ttl = await r.ttl(f"{PREFIX}:token:{tok}")
                sessions.append({
                    "scope": info.get("scope", scope),
                    "user_id": info.get("user_id", user_id),
                    "username": info.get("username", ""),
                    "token": tok[:16] + "…",
                    "ttl": max(ttl, 0),
                    "expires_at": int(time.time()) + max(ttl, 0),
                })
        if cursor == 0:
            break

    return ok(sessions)


async def session_kick(
    request: Request,
    auth: AuthInfo = Depends(current_auth),
    db: AsyncSession = Depends(get_db),
):
    """按 scope + user_id 踢下线（撤销全部 session）.

    越权防护: 非超管不可踢超管 (S1 修复)。
    """
    body = await request.json()
    scope = body.get("scope")
    user_id = body.get("user_id")
    if not scope or not user_id:
        return fail("缺少 scope / user_id")
    if scope not in ("admin", "client"):
        return fail("scope 不合法")

    # 非超管不可踢超管（防越权提权）
    if scope == "admin" and not auth.is_super_admin:
        from sqlalchemy import select
        from app.models.admin_user import AdminUser
        target = (await db.execute(select(AdminUser).where(AdminUser.id == user_id))).scalar_one_or_none()
        if target and target.is_super_admin:
            return fail("无权操作超级管理员", 403)

    await revoke_all_tokens(scope, user_id)
    return ok(msg="已踢下线")
