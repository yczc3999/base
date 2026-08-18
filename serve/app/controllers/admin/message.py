from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.database import get_db
from app.utils.response import ok
from app.logics.message import message_logic
from app.deps import AuthInfo, current_auth


class MarkReadDto(BaseModel):
    id: int = 0  # 0 = 全部标记已读


async def unread_count(auth: AuthInfo = Depends(current_auth), db: AsyncSession = Depends(get_db)):
    """未读消息数量"""
    count = await message_logic.unread_count(db, auth.user_id)
    return ok({"count": count})


async def mark_read(
    dto: MarkReadDto,
    auth: AuthInfo = Depends(current_auth),
    db: AsyncSession = Depends(get_db),
):
    """标记已读（id=0 全部已读）"""
    if dto.id == 0:
        await message_logic.mark_all_read(db, auth.user_id)
    else:
        await message_logic.mark_read(db, dto.id, auth.user_id)
    return ok(msg="操作成功")
