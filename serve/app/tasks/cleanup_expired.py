"""清理过期数据（每小时）"""

from app.tasks.base import BaseTask


class CleanupExpiredTask(BaseTask):
    name = "清理过期数据"
    interval = 3600

    async def run(self):
        from app.services.database import async_session
        from app.services.redis import cache_del_pattern
        from sqlalchemy import delete, text
        from datetime import datetime, timedelta

        async with async_session() as db:
            # 清理 30 天前的操作日志
            cutoff = datetime.now() - timedelta(days=30)
            from app.models import AdminOperationLog
            result = await db.execute(
                delete(AdminOperationLog).where(AdminOperationLog.created_at < cutoff)
            )
            await db.commit()

            if result.rowcount > 0:
                import logging
                logging.getLogger("task").info(f"Cleaned {result.rowcount} old operation logs")
