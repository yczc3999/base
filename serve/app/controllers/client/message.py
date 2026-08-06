"""前端用户消息 — client 端读取/已读.

base 无 client 前端页面, 此 API 供下游前端挂载。消息由 admin 端发送
(user_id = client 用户 id), client 端按当前登录用户过滤。
"""
from fastapi import APIRouter, Request, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import AuthInfo, require_client
from app.logics.message import message_logic
from app.services.database import get_db
from app.utils.response import ok, fail

router = APIRouter()


@router.get("/message/list")
async def message_list(
    request: Request,
    auth: AuthInfo = Depends(require_client),
    db: AsyncSession = Depends(get_db),
):
    """当前用户消息列表（bind_user_column 自动按 user_id 过滤）."""
    query = dict(request.query_params)
    result = await message_logic.get_list(db, query, user_id=auth.user_id)
    return ok(result)


@router.get("/message/unread")
async def message_unread(
    auth: AuthInfo = Depends(require_client),
    db: AsyncSession = Depends(get_db),
):
    """未读消息数."""
    count = await message_logic.unread_count(db, auth.user_id)
    return ok({"count": count})


@router.post("/message/read")
async def message_read(
    request: Request,
    auth: AuthInfo = Depends(require_client),
    db: AsyncSession = Depends(get_db),
):
    """标记单条已读（校验归属: 只能标记自己的消息）."""
    body = await request.json()
    message_id = body.get("id")
    if not message_id:
        return fail("缺少 id")
    await message_logic.mark_read(db, message_id, auth.user_id)
    return ok(msg="已标记已读")


@router.post("/message/readAll")
async def message_read_all(
    auth: AuthInfo = Depends(require_client),
    db: AsyncSession = Depends(get_db),
):
    """全部标记已读."""
    await message_logic.mark_all_read(db, auth.user_id)
    return ok(msg="已全部标记已读")
