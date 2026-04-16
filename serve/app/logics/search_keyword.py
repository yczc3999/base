from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.logics.base import BaseLogic
from app.models.search_keyword import SearchKeyword


class SearchKeywordLogic(BaseLogic):
    model = SearchKeyword
    cache_prefix = "search_keyword"

    def allowed_filters(self):
        return ["id", "status", "source", "tag_id", "seed_keyword"]

    def allowed_sorts(self):
        return ["id", "clicks", "impressions", "ctr", "position", "fetched_at", "created_at"]

    def keyword_fields(self):
        return ["keyword", "seed_keyword"]

    async def bulk_import(
        self,
        db: AsyncSession,
        items: list[dict],
    ) -> dict:
        """批量导入关键词（去重，跳过已存在的）。

        items: [{"keyword": str, "source": int, "seed_keyword": str}, ...]
        """
        imported = 0
        skipped = 0
        now = datetime.utcnow()

        # 复用 tag 的硬过滤（URL / 长句 / 异常标点一律跳过）
        from app.logics.tag import _is_valid_keyword

        for item in items:
            kw = item["keyword"].strip()
            if not kw or not _is_valid_keyword(kw):
                skipped += 1
                continue
            r = await db.execute(
                text(
                    "INSERT INTO search_keywords (keyword, source, seed_keyword, status, fetched_at) "
                    "VALUES (:kw, :src, :seed, 0, :now) "
                    "ON CONFLICT DO NOTHING "
                    "RETURNING id"
                ),
                {
                    "kw": kw,
                    "src": item.get("source", 0),
                    "seed": item.get("seed_keyword"),
                    "now": now,
                },
            )
            if r.scalar():
                imported += 1
            else:
                skipped += 1

        await db.commit()
        return {"imported": imported, "skipped": skipped}

    async def bulk_approve(self, db: AsyncSession, ids: list[int]) -> dict:
        """批量上线：status → 1，自动为每个关键词创建 tag。"""
        if not ids:
            return {"approved": 0, "tags_created": 0}

        rows = await db.execute(
            text(
                "SELECT id, keyword FROM search_keywords "
                "WHERE id = ANY(:ids) AND status != 1"
            ),
            {"ids": ids},
        )
        rows = rows.mappings().all()

        tags_created = 0
        for row in rows:
            kw = row["keyword"]
            slug = _to_slug(kw)

            existing_tag = await db.execute(
                text("SELECT id FROM tags WHERE name = :name OR slug = :slug LIMIT 1"),
                {"name": kw, "slug": slug},
            )
            tag_row = existing_tag.first()
            if tag_row:
                tag_id = tag_row[0]
            else:
                new_tag = await db.execute(
                    text(
                        "INSERT INTO tags (name, slug, source, status) "
                        "VALUES (:name, :slug, 2, 1) RETURNING id"
                    ),
                    {"name": kw, "slug": slug},
                )
                tag_id = new_tag.scalar()
                tags_created += 1

            await db.execute(
                text(
                    "UPDATE search_keywords SET status = 1, tag_id = :tag_id, "
                    "updated_at = NOW() WHERE id = :id"
                ),
                {"tag_id": tag_id, "id": row["id"]},
            )

        await db.commit()
        return {"approved": len(rows), "tags_created": tags_created}

    async def bulk_reject(self, db: AsyncSession, ids: list[int]) -> int:
        """批量忽略：status → 2"""
        if not ids:
            return 0
        r = await db.execute(
            text(
                "UPDATE search_keywords SET status = 2, updated_at = NOW() "
                "WHERE id = ANY(:ids) AND status = 0"
            ),
            {"ids": ids},
        )
        await db.commit()
        return r.rowcount

    async def get_unharvested(self, db: AsyncSession, limit: int = 20) -> list[dict]:
        """取未采集过的关键词（作为下一轮采集的种子）。

        只取 status=0（待审核）或 status=1（已上线）的，已忽略的不再采集。
        """
        r = await db.execute(
            text(
                "SELECT id, keyword FROM search_keywords "
                "WHERE harvested = FALSE AND status != 2 "
                "ORDER BY id LIMIT :limit"
            ),
            {"limit": limit},
        )
        return [dict(row) for row in r.mappings().all()]

    async def mark_harvested(self, db: AsyncSession, ids: list[int]) -> int:
        """标记为已采集"""
        if not ids:
            return 0
        r = await db.execute(
            text(
                "UPDATE search_keywords SET harvested = TRUE, updated_at = NOW() "
                "WHERE id = ANY(:ids)"
            ),
            {"ids": ids},
        )
        await db.commit()
        return r.rowcount

    async def stats(self, db: AsyncSession) -> dict:
        r = await db.execute(
            text("SELECT status, COUNT(*) FROM search_keywords GROUP BY status")
        )
        counts = {row[0]: row[1] for row in r.fetchall()}

        r2 = await db.execute(
            text("SELECT COUNT(*) FROM search_keywords WHERE harvested = FALSE AND status != 2")
        )
        unharvested = r2.scalar() or 0

        return {
            "pending": counts.get(0, 0),
            "approved": counts.get(1, 0),
            "rejected": counts.get(2, 0),
            "total": sum(counts.values()),
            "unharvested": unharvested,
        }


def _to_slug(kw: str) -> str:
    slug = kw.lower().strip()
    slug = re.sub(r'[^\w\s-]', '', slug)
    slug = re.sub(r'[\s_]+', '-', slug)
    slug = re.sub(r'-+', '-', slug).strip('-')
    return slug[:50] if slug else "tag"


search_keyword_logic = SearchKeywordLogic()
