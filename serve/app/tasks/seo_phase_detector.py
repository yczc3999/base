"""SEO 阶段探测器（每日 01:00 UTC）

读 site.launched_at + articles 统计 + GSC indexed_cnt（如配置）→ 算阶段
→ 写回 settings.seo.current_phase 等 → 同时 publish_log 记一笔
"""
import logging
from datetime import datetime, timezone

from app.tasks.base import BaseTask
from app.services.database import async_session
from app.services.seo_phase import seo_phase_service
from app.logics.publish_log import publish_log_logic
from app.logics.setting import setting_logic

logger = logging.getLogger("task")

TARGET_HOUR_UTC = 1


class SeoPhaseDetectorTask(BaseTask):
    name = "SEO 阶段探测"
    interval = 3600  # 每小时检查；只在 TARGET_HOUR_UTC 实际跑

    async def run(self):
        now = datetime.now(timezone.utc)
        if now.hour != TARGET_HOUR_UTC:
            return  # 不到点，跳过

        async with async_session() as db:
            prev = await setting_logic.get(db, "seo", "current_phase", "cold")
            result = await seo_phase_service.detect_and_persist(db)
            if prev != result["phase"]:
                # 用 write_no_commit：phase_change 日志和 settings 写入算同一逻辑事务
                # （即使 settings.set 内部已 commit，本条日志仍在同一 session 内立即可见）
                await publish_log_logic.write_no_commit(
                    db, action="phase_change", level="info",
                    msg=f"phase: {prev} → {result['phase']}",
                    payload={"from": prev, "to": result["phase"], "reason": result["reason"],
                             "age_days": result["age_days"], "published_cnt": result["published_cnt"],
                             "indexed_cnt": result["indexed_cnt"], "health_score": result["health_score"]},
                )
                await db.commit()

        logger.info("[SEO phase] %s (%s); quota=%s",
                    result["phase"], result["reason"], result["quota"])

        # 顺手清 publish_log 历史（info 90 天 / error 365 天，避免无人维护无限增长）
        async with async_session() as db:
            cleanup_result = await publish_log_logic.cleanup(db)
        if cleanup_result["info_deleted"] + cleanup_result["error_deleted"] > 0:
            logger.info("[SEO phase] log cleanup: %s", cleanup_result)
