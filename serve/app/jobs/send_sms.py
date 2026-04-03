"""异步发送短信"""

from app.jobs.base import BaseJob


class SendSmsJob(BaseJob):
    name = "send_sms"

    async def handle(self, data: dict):
        from app.services.sms import sms_service
        from app.services.database import async_session

        phone = data.get("phone")
        code = data.get("code")
        if not phone or not code:
            return

        async with async_session() as db:
            await sms_service.send_code(db, phone, code)
            if sms_service.failed:
                import logging
                logging.getLogger("job").error(f"SMS failed: {sms_service.error}")
