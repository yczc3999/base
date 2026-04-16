"""SEO 排期任务（每日 02:30 UTC）

调 SeoSchedulerService.assign_pending() 把已 AI 润色的草稿派 scheduled_at。
"""
import logging
from datetime import datetime, timezone

from app.tasks.base import BaseTask
from app.services.database import async_session
from app.services.seo_scheduler import seo_scheduler_service

logger = logging.getLogger("task")

TARGET_HOUR_UTC = 2
TARGET_MIN_UTC = 30


class SeoSchedulerTask(BaseTask):
    name = "SEO 发布排期"
    interval = 1800  # 每 30 分钟检查；只在 02:30 实际跑

    async def run(self):
        now = datetime.now(timezone.utc)
        if not (now.hour == TARGET_HOUR_UTC and now.minute >= TARGET_MIN_UTC and now.minute < TARGET_MIN_UTC + 30):
            return

        from app.services.seo_phase import is_seo_enabled
        async with async_session() as db:
            if not await is_seo_enabled(db):
                return  # SEO 总开关关闭
            result = await seo_scheduler_service.assign_pending(db)

        logger.info("[SEO scheduler] phase=%s scheduled=%d needs_review=%d skipped=%d",
                    result["phase"], result["scheduled"],
                    result["needs_review"], result["skipped"])
