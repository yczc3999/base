"""清理过期数据（每小时）"""

import logging
from datetime import datetime, timezone, timedelta
from sqlalchemy import delete
from app.tasks.base import BaseTask
from app.services.database import async_session
from app.models import AdminOperationLog

logger = logging.getLogger("task")


class CleanupExpiredTask(BaseTask):
    name = "清理过期数据"
    interval = 3600

    async def run(self):
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)

        async with async_session() as db:
            result = await db.execute(
                delete(AdminOperationLog).where(AdminOperationLog.created_at < cutoff)
            )
            await db.commit()

            if result.rowcount > 0:
                logger.info(f"Cleaned {result.rowcount} old operation logs")
