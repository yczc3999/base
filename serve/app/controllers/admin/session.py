"""在线会话管理 — 查看活跃 session 列表 + 一键踢下线.

数据源: Redis user_tokens 索引 (SCAN 枚举) + token 详情 (username/scope/ttl)。
"""
import json
import time

from fastapi import APIRouter, Request, Depends

from app.config import settings
from app.deps import AuthInfo, require_perms
from app.services.redis import get_redis
from app.utils.response import ok, fail
from app.utils.token import revoke_all_tokens

router = APIRouter()

_perm_list = require_perms("admin:session:list")
_perm_kick = require_perms("admin:session:kick")

PREFIX = settings.APP_NAME


@router.get("/session/list")
async def session_list(auth: AuthInfo = Depends(_perm_list)):
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


@router.post("/session/kick")
async def session_kick(request: Request, auth: AuthInfo = Depends(_perm_kick)):
    """按 scope + user_id 踢下线（撤销全部 session）."""
    body = await request.json()
    scope = body.get("scope")
    user_id = body.get("user_id")
    if not scope or not user_id:
        return fail("缺少 scope / user_id")
    if scope not in ("admin", "client"):
        return fail("scope 不合法")

    await revoke_all_tokens(scope, user_id)
    return ok(msg="已踢下线")
