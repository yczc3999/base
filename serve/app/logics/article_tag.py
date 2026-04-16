"""article_tag logic — 文章×标签多对多关系

"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.logics.base import BaseLogic
from app.models.article_tag import ArticleTag


class ArticleTagLogic(BaseLogic):
    model = ArticleTag
    cache_prefix = "article_tag"

    def allowed_filters(self):
        return ["article_id", "tag_id", "is_primary"]

    def allowed_sorts(self):
        return ["created_at"]

    def keyword_fields(self):
        return []

    # ===== 业务方法 =====

    async def set_tags(
        self, db: AsyncSession, article_id: int, tag_ids: list[int],
        primary_tag_id: int | None = None,
    ) -> int:
        """覆盖式设置文章的 tag 列表。返回写入条数。
        primary_tag_id 必须在 tag_ids 中，否则忽略。
        """
        if not tag_ids:
            await db.execute(
                text("DELETE FROM article_tags WHERE article_id = :a"),
                {"a": article_id},
            )
            await db.commit()
            return 0

        await db.execute(
            text("DELETE FROM article_tags WHERE article_id = :a"),
            {"a": article_id},
        )
        for tid in tag_ids:
            is_primary = (tid == primary_tag_id)
            await db.execute(text(
                "INSERT INTO article_tags (article_id, tag_id, is_primary) "
                "VALUES (:a, :t, :p) ON CONFLICT DO NOTHING"
            ), {"a": article_id, "t": tid, "p": is_primary})
        await db.commit()
        return len(tag_ids)

    async def get_tags_for_article(self, db: AsyncSession, article_id: int) -> list[dict]:
        r = await db.execute(text(
            "SELECT t.id, t.name, t.slug, at.is_primary "
            "FROM article_tags at JOIN tags t ON t.id = at.tag_id "
            "WHERE at.article_id = :a "
            "ORDER BY at.is_primary DESC, t.name"
        ), {"a": article_id})
        return [dict(row) for row in r.mappings().all()]

    async def get_articles_for_tag(
        self, db: AsyncSession, tag_id: int, days: int | None = None,
    ) -> list[dict]:
        """某 tag 下的文章；days 不为空时限定窗口。"""
        sql = (
            "SELECT a.id, a.title, a.slug, a.published_at, at.is_primary "
            "FROM article_tags at JOIN articles a ON a.id = at.article_id "
            "WHERE at.tag_id = :t AND a.deleted_at IS NULL "
            + ("AND a.created_at > NOW() - (:d || ' days')::INTERVAL " if days else "")
            + "ORDER BY a.created_at DESC"
        )
        params: dict = {"t": tag_id}
        if days:
            params["d"] = str(days)
        r = await db.execute(text(sql), params)
        return [dict(row) for row in r.mappings().all()]

    async def count_published_for_tag_recent(
        self, db: AsyncSession, tag_id: int, days: int = 7,
    ) -> int:
        """某 tag 在最近 N 天内的"已发布且已到时间"的文章数 —— 用于反 Doorway 限次。"""
        r = await db.execute(text(
            "SELECT COUNT(*) FROM article_tags at "
            "JOIN articles a ON a.id = at.article_id "
            "WHERE at.tag_id = :t "
            "AND a.status = 1 AND a.deleted_at IS NULL "
            "AND a.published_at IS NOT NULL "
            "AND a.published_at > NOW() - (:d || ' days')::INTERVAL "
            "AND a.published_at <= NOW()"
        ), {"t": tag_id, "d": str(days)})
        return int(r.scalar() or 0)


article_tag_logic = ArticleTagLogic()
