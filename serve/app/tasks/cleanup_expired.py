"""清理过期数据（每小时）"""

import os
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

        # 清理过期导出文件（超过 2 小时的）
        self._cleanup_export_files()

    @staticmethod
    def _cleanup_export_files():
        import tempfile
        export_dir = os.path.join(tempfile.gettempdir(), "base_exports")
        if not os.path.isdir(export_dir):
            return

        cutoff = datetime.now().timestamp() - 7200  # 2 小时前
        cleaned = 0
        for f in os.listdir(export_dir):
            fp = os.path.join(export_dir, f)
            if os.path.isfile(fp) and os.path.getmtime(fp) < cutoff:
                os.remove(fp)
                cleaned += 1

        if cleaned > 0:
            logger.info(f"Cleaned {cleaned} expired export files")
