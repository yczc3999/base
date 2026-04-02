from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.message import Message
from app.logics.base import BaseLogic


class MessageLogic(BaseLogic):
    model = Message
    cache_prefix = ""
    bind_user_column = "user_id"

    def allowed_filters(self):
        return ["id", "user_id", "type", "is_read", "created_at"]

    def allowed_sorts(self):
        return ["id", "created_at", "is_read"]

    def keyword_fields(self):
        return ["title", "content"]

    async def mark_read(self, db: AsyncSession, message_id: int, user_id: int):
        """标记单条已读"""
        stmt = (
            update(Message)
            .where(Message.id == message_id, Message.user_id == user_id)
            .values(is_read=True)
        )
        await db.execute(stmt)
        await db.commit()

    async def mark_all_read(self, db: AsyncSession, user_id: int):
        """全部标记已读"""
        stmt = (
            update(Message)
            .where(Message.user_id == user_id, Message.is_read == False)
            .values(is_read=True)
        )
        await db.execute(stmt)
        await db.commit()

    async def unread_count(self, db: AsyncSession, user_id: int) -> int:
        """未读数量"""
        stmt = select(func.count()).select_from(Message).where(
            Message.user_id == user_id, Message.is_read == False
        )
        result = await db.execute(stmt)
        return result.scalar_one()

    async def send(self, db: AsyncSession, user_id: int, title: str, content: str = "",
                   msg_type: int = Message.Type.SYSTEM, sender_id: int = None, sender_name: str = None):
        """发送消息"""
        msg = Message(
            user_id=user_id, title=title, content=content,
            type=msg_type, sender_id=sender_id, sender_name=sender_name,
        )
        db.add(msg)
        await db.commit()


message_logic = MessageLogic()
