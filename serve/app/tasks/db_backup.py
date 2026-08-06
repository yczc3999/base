"""数据库备份（每天）"""

import logging
from app.tasks.base import BaseTask

logger = logging.getLogger("task")


class DbBackupTask(BaseTask):
    name = "数据库备份"
    interval = 86400  # 每天

    async def run(self):
        from app.services.database import async_session
        from app.logics.db_backup import db_backup_logic

        async with async_session() as db:
            # 不吞异常: 让 BaseTask.execute() 捕获并记录 last_run.status=failed (L1 修复)
            result = await db_backup_logic.do_backup(db)
            logger.info(f"DB backup done: {result['filename']} ({result['file_size']} bytes)")
