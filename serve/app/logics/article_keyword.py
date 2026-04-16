"""文章 ↔ 关键词 多对多关系 (替代原 article_tag_logic).

与原 article_tag_logic 功能等价, 字段名 tag_id → keyword_id.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.logics.base import BaseLogic
from app.models.article_keyword import ArticleKeyword


class ArticleKeywordLogic(BaseLogic):
    model = ArticleKeyword
    cache_prefix = "article_keyword"
    except_keys = ["created_at"]

    def allowed_filters(self):
        return ["article_id", "keyword_id", "is_primary"]

    async def replace_for_article(
        self,
        db: AsyncSession,
        article_id: int,
        keyword_ids: list[int],
        primary_id: int | None = None,
    ) -> None:
        """覆盖式替换一篇文章的关键词绑定."""
        if not keyword_ids:
            await db.execute(
                text("DELETE FROM article_keywords WHERE article_id = :a"),
                {"a": article_id},
            )
            await db.commit()
            return

        await db.execute(
            text("DELETE FROM article_keywords WHERE article_id = :a"),
            {"a": article_id},
        )
        for kid in keyword_ids:
            await db.execute(
                text(
                    "INSERT INTO article_keywords (article_id, keyword_id, is_primary) "
                    "VALUES (:a, :k, :p) ON CONFLICT DO NOTHING"
                ),
                {"a": article_id, "k": kid, "p": (kid == primary_id)},
            )
        await db.commit()

    async def get_keywords_for_article(
        self, db: AsyncSession, article_id: int,
    ) -> list[dict]:
        r = await db.execute(
            text(
                "SELECT k.id, k.keyword AS name, k.slug, ak.is_primary "
                "FROM article_keywords ak JOIN keywords k ON k.id = ak.keyword_id "
                "WHERE ak.article_id = :a ORDER BY ak.is_primary DESC, k.id"
            ),
            {"a": article_id},
        )
        return [dict(row) for row in r.mappings().all()]

    async def get_articles_for_keyword(
        self, db: AsyncSession, keyword_id: int, limit: int = 20,
    ) -> list[dict]:
        r = await db.execute(
            text(
                "SELECT a.id, a.title, a.slug, a.published_at "
                "FROM article_keywords ak JOIN articles a ON a.id = ak.article_id "
                "WHERE ak.keyword_id = :k AND a.status = 1 AND a.deleted_at IS NULL "
                "ORDER BY ak.created_at DESC LIMIT :n"
            ),
            {"k": keyword_id, "n": limit},
        )
        return [dict(row) for row in r.mappings().all()]

    async def count_published_for_keyword_recent(
        self, db: AsyncSession, keyword_id: int, days: int = 7,
    ) -> int:
        r = await db.execute(
            text(
                "SELECT COUNT(*) FROM article_keywords ak "
                "JOIN articles a ON a.id = ak.article_id "
                "WHERE ak.keyword_id = :k AND a.status = 1 "
                "  AND a.deleted_at IS NULL "
                "  AND a.published_at > NOW() - make_interval(days => :d)"
            ),
            {"k": keyword_id, "d": days},
        )
        return r.scalar() or 0


article_keyword_logic = ArticleKeywordLogic()
