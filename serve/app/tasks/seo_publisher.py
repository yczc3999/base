"""SEO 发布执行器（v2，每 5 分钟）

直接扫 articles WHERE status=0 AND scheduled_at<=NOW，
不再有 publish_schedule 中间表 + 锁。

紧急禁发 Redis 实时检查。
"""
import logging

from app.tasks.base import BaseTask
from app.services.database import async_session
from app.services.redis import get_redis
from app.services.seo_publish import seo_publish_service
from app.logics.article import article_logic

logger = logging.getLogger("task")


class SeoPublisherTask(BaseTask):
    name = "SEO 发布执行器"
    interval = 300

    async def _kill_switch_on(self) -> bool:
        try:
            r = await get_redis()
            v = await r.get("seo:kill_switch")
            return v in (b"1", "1", b"true", "true")
        except Exception:
            return False

    async def run(self):
        from app.services.seo_phase import is_seo_enabled
        async with async_session() as db:
            if not await is_seo_enabled(db):
                return  # SEO 总开关关闭，静默跳过
        if await self._kill_switch_on():
            logger.info("[SEO publisher] kill_switch ON, skip")
            return

        async with async_session() as db:
            tasks = await article_logic.get_due_for_publish(db, limit=5)
            if not tasks:
                return
            published = skipped = errored = 0
            for t in tasks:
                res = await seo_publish_service.publish_one(db, t["id"], source="auto")
                s = res.get("status")
                if s == "published":
                    published += 1
                elif s == "skipped":
                    skipped += 1
                else:
                    errored += 1

        logger.info("[SEO publisher] processed=%d published=%d skipped=%d errored=%d",
                    len(tasks), published, skipped, errored)
