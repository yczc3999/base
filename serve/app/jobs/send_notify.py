"""异步发送通知"""

from app.jobs.base import BaseJob


class SendNotifyJob(BaseJob):
    name = "send_notify"

    async def handle(self, data: dict):
        from app.services.notify import notify_service
        from app.services.database import async_session

        title = data.get("title", "")
        content = data.get("content", "")
        channel = data.get("channel")  # 指定渠道，None=默认

        async with async_session() as db:
            if channel:
                await notify_service.send_by(db, channel, title, content)
            else:
                await notify_service.send(db, title, content)
