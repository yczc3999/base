from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.logics.base import BaseLogic
from app.models.article import Article


class ArticleLogic(BaseLogic):
    model = Article
    cache_prefix = "article"
    except_keys = ["updated_at"]

    def allowed_filters(self):
        return ["id", "status", "source", "ai_processed"]

    def allowed_sorts(self):
        return ["id", "sort", "created_at", "published_at", "view_count"]

    def keyword_fields(self):
        return ["title", "slug", "summary"]

    def _auto_publish_at(self, data: dict) -> dict:
        """发布时若 published_at 为空，自动填当前时间；允许用户手动指定（穿越时空）。"""
        status = data.get("status")
        if status is not None and int(status) == 1 and not data.get("published_at"):
            data["published_at"] = datetime.utcnow()
        return data

    def _maybe_simhash(self, data: dict) -> dict:
        """文章内容变更时同步算 simhash。
        simhash 是文章内在属性（内容指纹），与 published_at/updated_at 同等地位，
        由 article_logic 全权维护。SEO 模块只读不写。
        """
        content = data.get("content")
        if content:
            from app.services.seo_simhash import simhash64
            sh = simhash64(content)
            # PostgreSQL BIGINT 是 signed，正确转换正数到 signed 表示
            data["simhash"] = sh - (1 << 64) if sh >= (1 << 63) else sh
        return data

    def before_create(self, data: dict) -> dict:
        return self._maybe_simhash(self._auto_publish_at(data))

    def before_edit(self, data: dict) -> dict:
        return self._maybe_simhash(self._auto_publish_at(data))

    async def import_collected(
        self,
        db: AsyncSession,
        title: str,
        content: str,
        source_url: str,
        author: str | None = None,
        date: str | None = None,
    ) -> int | None:
        """采集入库（ON CONFLICT 去重）"""
        from app.services.seo_simhash import simhash64
        slug = _to_slug(title)
        sh = simhash64(content)
        sh_signed = sh - (1 << 64) if sh >= (1 << 63) else sh
        r = await db.execute(
            text(
                "INSERT INTO articles "
                "(title, slug, content, raw_content, source_url, source, status, sort, "
                " ai_processed, simhash) "
                "VALUES (:title, :slug, :content, :raw, :url, 1, 0, 0, FALSE, :sh) "
                "ON CONFLICT (source_url) DO NOTHING "
                "RETURNING id"
            ),
            {
                "title": title[:200],
                "slug": slug,
                "content": content,
                "raw": content,
                "url": source_url,
                "sh": sh_signed,
            },
        )
        return r.scalar()

    # ===== v2 SEO 状态查询封装（外部不直接 WHERE，避免 scheduled_at 语义重载出错）=====

    async def list_missing_simhash(self, db: AsyncSession, limit: int = 50) -> list[dict]:
        """SEO pipeline 用：simhash IS NULL 的文章，补指纹。"""
        r = await db.execute(text(
            "SELECT id, title, content FROM articles "
            "WHERE simhash IS NULL AND deleted_at IS NULL "
            "ORDER BY id DESC LIMIT :n"
        ), {"n": limit})
        return [dict(row) for row in r.mappings().all()]

    async def insert_ai_generated(
        self, db: AsyncSession,
        title: str, slug: str | None, summary: str, content: str,
        simhash: int | None = None,
    ) -> int | None:
        """pipeline AI 生成文章入库。status=0 草稿，ai_processed=TRUE。
        slug 冲突时返 None 不插入。
        """
        r = await db.execute(text(
            "INSERT INTO articles "
            "(title, slug, summary, content, source, ai_processed, status, sort, simhash) "
            "VALUES (:title, :slug, :summary, :content, 0, TRUE, 0, 0, :sh) "
            "ON CONFLICT (slug) DO NOTHING "
            "RETURNING id"
        ), {"title": title[:200], "slug": slug or None,
            "summary": (summary or "")[:500], "content": content, "sh": simhash})
        await db.commit()
        return r.scalar()

    async def get_drafts(self, db: AsyncSession, limit: int = 20) -> list[dict]:
        """status=0 + scheduled_at IS NULL + 未删除 + 未排期"""
        r = await db.execute(text(
            "SELECT id, title, created_at FROM articles "
            "WHERE status = 0 AND deleted_at IS NULL AND scheduled_at IS NULL "
            "ORDER BY created_at DESC LIMIT :n"
        ), {"n": limit})
        return [dict(row) for row in r.mappings().all()]

    async def get_due_for_publish(self, db: AsyncSession, limit: int = 20) -> list[dict]:
        """worker publisher 调：status=0 + scheduled_at <= NOW + 未删除"""
        r = await db.execute(text(
            "SELECT id, title FROM articles "
            "WHERE status = 0 AND deleted_at IS NULL "
            "AND scheduled_at IS NOT NULL AND scheduled_at <= NOW() "
            "ORDER BY scheduled_at ASC LIMIT :n"
        ), {"n": limit})
        return [dict(row) for row in r.mappings().all()]

    async def get_scheduled(self, db: AsyncSession, limit: int = 50) -> list[dict]:
        """status=0 + scheduled_at IS NOT NULL + 未来时间 = 排队中（dashboard 用）"""
        r = await db.execute(text(
            "SELECT id, title, scheduled_at FROM articles "
            "WHERE status = 0 AND deleted_at IS NULL "
            "AND scheduled_at IS NOT NULL AND scheduled_at > NOW() "
            "ORDER BY scheduled_at ASC LIMIT :n"
        ), {"n": limit})
        return [dict(row) for row in r.mappings().all()]

    async def stats(self, db: AsyncSession) -> dict:
        """Dashboard 用：5 个核心计数"""
        r = await db.execute(text(
            "SELECT "
            "  COUNT(*) FILTER (WHERE status = 0 AND scheduled_at IS NULL AND deleted_at IS NULL) AS drafts, "
            "  COUNT(*) FILTER (WHERE status = 0 AND scheduled_at IS NOT NULL AND scheduled_at > NOW() AND deleted_at IS NULL) AS scheduled, "
            "  COUNT(*) FILTER (WHERE status = 1 AND deleted_at IS NULL) AS published, "
            "  COUNT(*) FILTER (WHERE status = 0 AND last_publish_error IS NOT NULL AND deleted_at IS NULL) AS failed, "
            "  COUNT(*) FILTER (WHERE deleted_at IS NOT NULL) AS deleted "
            "FROM articles"
        ))
        row = r.mappings().first()
        nd = await db.execute(text(
            "SELECT MIN(scheduled_at) FROM articles "
            "WHERE status = 0 AND scheduled_at IS NOT NULL AND scheduled_at > NOW() AND deleted_at IS NULL"
        ))
        return {
            "drafts": int(row["drafts"] or 0),
            "scheduled": int(row["scheduled"] or 0),
            "published": int(row["published"] or 0),
            "failed": int(row["failed"] or 0),
            "deleted": int(row["deleted"] or 0),
            "next_due": nd.scalar(),
        }

    async def get_with_raw(self, db: AsyncSession, article_id: int) -> dict | None:
        """取文章含原始内容（供 AI 润色用）"""
        r = await db.execute(
            text("SELECT id, title, content, raw_content FROM articles WHERE id = :id"),
            {"id": article_id},
        )
        row = r.mappings().first()
        return dict(row) if row else None

    async def collect_stats(self, db: AsyncSession) -> dict:
        r = await db.execute(text(
            "SELECT "
            "COUNT(*) FILTER (WHERE source = 1) as collected, "
            "COUNT(*) FILTER (WHERE source = 1 AND ai_processed = FALSE) as unprocessed, "
            "COUNT(*) FILTER (WHERE source = 1 AND ai_processed = TRUE) as ai_done, "
            "COUNT(*) FILTER (WHERE status = 1) as published "
            "FROM articles"
        ))
        return dict(r.mappings().first())

    async def list_collected_unprocessed(self, db: AsyncSession, limit: int = 20) -> list[dict]:
        """取采集但未 AI 处理的文章"""
        r = await db.execute(
            text(
                "SELECT id, title, source_url, "
                "LENGTH(content) as content_len, ai_processed "
                "FROM articles WHERE source = 1 AND ai_processed = FALSE "
                "ORDER BY id LIMIT :limit"
            ),
            {"limit": limit},
        )
        return [dict(row) for row in r.mappings().all()]

    async def update_ai_content(
        self,
        db: AsyncSession,
        article_id: int,
        title: str,
        content: str,
        summary: str,
        slug: str,
    ) -> None:
        """AI 润色后更新文章 —— content 变了同步算 simhash。"""
        from app.services.seo_simhash import simhash64
        sh = simhash64(content)
        sh_signed = sh - (1 << 64) if sh >= (1 << 63) else sh
        await db.execute(
            text(
                "UPDATE articles SET "
                "title = :title, content = :content, summary = :summary, "
                "slug = :slug, ai_processed = TRUE, simhash = :sh, updated_at = NOW() "
                "WHERE id = :id"
            ),
            {"title": title, "content": content, "summary": summary,
             "slug": slug, "id": article_id, "sh": sh_signed},
        )


def _to_slug(title: str) -> str:
    slug = title.lower().strip()
    slug = re.sub(r'[^\w\s-]', '', slug)
    slug = re.sub(r'[\s_]+', '-', slug)
    slug = re.sub(r'-+', '-', slug).strip('-')
    return slug[:200] if slug else None


article_logic = ArticleLogic()
