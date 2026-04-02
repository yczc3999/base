from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.database import get_db
from app.utils.response import ok
from app.logics.message import message_logic
from app.controllers.base import crud_router
from app.deps import AuthInfo, require_admin, require_perms

router = APIRouter()

# CRUD（自动绑定 user_id，普通用户只能看自己的消息）
router.include_router(crud_router(
    "message", message_logic,
    tags=["admin-message"],
    auth_dep=require_admin,
    perms_prefix="admin:message",
))


class MarkReadDto(BaseModel):
    id: int = 0  # 0 = 全部标记已读


@router.get("/message/unreadCount")
async def unread_count(auth: AuthInfo = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """未读消息数量"""
    count = await message_logic.unread_count(db, auth.user_id)
    return ok({"count": count})


@router.post("/message/markRead")
async def mark_read(
    dto: MarkReadDto,
    auth: AuthInfo = Depends(require_admin),
    _: None = Depends(require_perms("admin:message:read")),
    db: AsyncSession = Depends(get_db),
):
    """标记已读（id=0 全部已读）"""
    if dto.id == 0:
        await message_logic.mark_all_read(db, auth.user_id)
    else:
        await message_logic.mark_read(db, dto.id, auth.user_id)
    return ok(msg="操作成功")
