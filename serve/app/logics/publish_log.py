"""publish_log logic — SEO 模块所有动作的时间线

只插入 + 查询，不更新（日志不可改）。
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.logics.base import BaseLogic
from app.models.publish_log import PublishLog


class PublishLogLogic(BaseLogic):
    model = PublishLog
    cache_prefix = "publish_log"
    # 列表/详情用，新增/编辑用不上
    except_keys = ["created_at"]

    def allowed_filters(self):
        return ["id", "action", "level", "article_id"]

    def allowed_sorts(self):
        return ["id", "created_at"]

    def keyword_fields(self):
        return ["msg"]

    # ===== 业务方法 =====

    async def _insert(
        self, db: AsyncSession,
        action: str, level: str = "info",
        article_id: int | None = None,
        msg: str = "", payload: dict | None = None,
    ) -> int:
        """裸插入；commit 由调用方控制。"""
        import json as _json
        # 注意：text() 里 ::jsonb 的 :: 会被当成 named param 前缀，必须 \:\: 转义
        r = await db.execute(text(
            "INSERT INTO publish_log (action, level, article_id, msg, payload) "
            "VALUES (:a, :l, :aid, :m, CAST(:p AS jsonb)) RETURNING id"
        ), {"a": action[:32], "l": level[:8], "aid": article_id,
            "m": (msg or "")[:1000],
            "p": _json.dumps(payload, ensure_ascii=False) if payload else None})
        return r.scalar()

    async def write(
        self, db: AsyncSession,
        action: str, level: str = "info",
        article_id: int | None = None,
        msg: str = "", payload: dict | None = None,
    ) -> int:
        """写一条日志（内部 commit）。日志属于审计语义，
        即使主业务事务回滚也应留下记录，因此独立 commit。
        对齐 setting_logic.set 等其他 logic 内部 commit 的项目惯例。
        """
        log_id = await self._insert(db, action, level, article_id, msg, payload)
        await db.commit()
        return log_id

    async def write_no_commit(
        self, db: AsyncSession,
        action: str, level: str = "info",
        article_id: int | None = None,
        msg: str = "", payload: dict | None = None,
    ) -> int:
        """加入主事务（与业务操作同生共死）。
        用于：日志和业务变更必须原子（如 phase_change 必须和 settings 写入同事务）。
        """
        return await self._insert(db, action, level, article_id, msg, payload)

    async def last_indexnow_at_for(
        self, db: AsyncSession, article_id: int,
    ):
        """查某文章最近一次 IndexNow 成功 ping 的时间。给"10 分钟冷却"判断用。"""
        r = await db.execute(text(
            "SELECT MAX(created_at) FROM publish_log "
            "WHERE action = 'indexnow' AND level = 'info' AND article_id = :id"
        ), {"id": article_id})
        return r.scalar()

    async def recent_errors(self, db: AsyncSession, hours: int = 24, limit: int = 50) -> list[dict]:
        """近 N 小时的错误日志（dashboard 告警用）。"""
        r = await db.execute(text(
            "SELECT id, action, article_id, msg, created_at FROM publish_log "
            "WHERE level = 'error' AND created_at > NOW() - (:h || ' hours')::INTERVAL "
            "ORDER BY created_at DESC LIMIT :n"
        ), {"h": str(hours), "n": limit})
        return [dict(row) for row in r.mappings().all()]

    async def cleanup(self, db: AsyncSession) -> dict:
        """定期清理：info 级 90 天、error 级 365 天。"""
        r1 = await db.execute(text(
            "DELETE FROM publish_log WHERE level = 'info' "
            "AND created_at < NOW() - INTERVAL '90 days'"
        ))
        r2 = await db.execute(text(
            "DELETE FROM publish_log WHERE level = 'error' "
            "AND created_at < NOW() - INTERVAL '365 days'"
        ))
        await db.commit()
        return {"info_deleted": r1.rowcount, "error_deleted": r2.rowcount}


publish_log_logic = PublishLogLogic()
