from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.logics.base import BaseLogic
from app.models.tag import Tag


class TagLogic(BaseLogic):
    model = Tag
    cache_prefix = "tag"
    except_keys = ["created_at", "updated_at"]

    def allowed_filters(self):
        return ["id", "status", "source", "harvested"]

    def allowed_sorts(self):
        return ["id", "sort", "article_count", "fetched_at", "created_at"]

    def keyword_fields(self):
        return ["name", "slug", "seed_keyword"]

    # ---- 批量操作 ----

    async def bulk_set_status(self, db: AsyncSession, ids: list[int], status: int) -> int:
        if not ids:
            return 0
        r = await db.execute(
            text("UPDATE tags SET status = :status, updated_at = NOW() WHERE id = ANY(:ids)"),
            {"status": status, "ids": ids},
        )
        await db.commit()
        await self.clear_cache_batch(ids)
        return r.rowcount

    async def bulk_approve(self, db: AsyncSession, ids: list[int]) -> int:
        """批量上线：status → 1，自动生成 slug（如果没有）"""
        if not ids:
            return 0
        r = await db.execute(
            text("SELECT id, name, slug FROM tags WHERE id = ANY(:ids) AND status != 1"),
            {"ids": ids},
        )
        rows = r.mappings().all()
        for row in rows:
            slug = row["slug"] or _to_slug(row["name"])
            await db.execute(
                text("UPDATE tags SET status = 1, slug = :slug, updated_at = NOW() WHERE id = :id"),
                {"slug": slug, "id": row["id"]},
            )
        await db.commit()
        return len(rows)

    async def bulk_reject(self, db: AsyncSession, ids: list[int]) -> int:
        if not ids:
            return 0
        r = await db.execute(
            text("UPDATE tags SET status = 2, updated_at = NOW() WHERE id = ANY(:ids) AND status = 0"),
            {"ids": ids},
        )
        await db.commit()
        return r.rowcount

    # ---- 采集 ----

    async def bulk_import(self, db: AsyncSession, items: list[dict]) -> dict:
        """批量导入采集结果（去重，跳过已存在的）。

        items: [{"keyword": str, "source": int, "seed_keyword": str}, ...]

        采用 ON CONFLICT DO NOTHING —— 即使 name/slug 撞了唯一约束也不会报错打断
        整个事务（之前因 slug 冲突一条异常导致整批回滚，"采集了但没入库"）。
        slug 追加 md5 前缀保证唯一（Chinese-only 等纯非 ASCII keyword 的兜底）。
        """
        import hashlib

        imported = 0
        skipped = 0
        now = datetime.utcnow()
        for item in items:
            kw = item["keyword"].strip()
            if not kw or not _is_valid_keyword(kw):
                skipped += 1
                continue
            base_slug = _to_slug(kw)
            # 纯非 ASCII 时 base_slug 可能全中文/变空；拼 6 位 hash 保唯一又可读
            if not base_slug or not any(c.isascii() and c.isalnum() for c in base_slug):
                base_slug = (base_slug + "-" if base_slug else "") + \
                    hashlib.md5(kw.encode()).hexdigest()[:6]
            slug = base_slug[:50]

            r = await db.execute(
                text(
                    "INSERT INTO tags (name, slug, source, seed_keyword, harvested, status, fetched_at) "
                    "VALUES (:name, :slug, :src, :seed, FALSE, 0, :now) "
                    "ON CONFLICT DO NOTHING "
                    "RETURNING id"
                ),
                {"name": kw, "slug": slug, "src": item.get("source", 0),
                 "seed": item.get("seed_keyword"), "now": now},
            )
            if r.scalar():
                imported += 1
            else:
                skipped += 1
        await db.commit()
        return {"imported": imported, "skipped": skipped}

    async def get_unharvested(self, db: AsyncSession, limit: int = 20) -> list[dict]:
        """取未采集过的标签作为下一轮种子（跳过已忽略的）。

        全池随机（摇匀后取前 N 个），避免按 id 顺序时陷入某个热门父标签无限下钻。
        """
        r = await db.execute(
            text(
                "SELECT id, name FROM tags "
                "WHERE harvested = FALSE AND status != 2 "
                "ORDER BY RANDOM() LIMIT :limit"
            ),
            {"limit": limit},
        )
        return [dict(row) for row in r.mappings().all()]

    async def mark_harvested(self, db: AsyncSession, ids: list[int]) -> int:
        if not ids:
            return 0
        r = await db.execute(
            text("UPDATE tags SET harvested = TRUE, updated_at = NOW() WHERE id = ANY(:ids)"),
            {"ids": ids},
        )
        await db.commit()
        return r.rowcount

    async def get_pending_batch(self, db: AsyncSession, after_id: int = 0, limit: int = 30) -> list[dict]:
        """取一批待审核标签（用 id > after_id 避免死循环）"""
        r = await db.execute(
            text("SELECT id, name FROM tags WHERE status = 0 AND id > :after ORDER BY id LIMIT :limit"),
            {"after": after_id, "limit": limit},
        )
        return [dict(row) for row in r.mappings().all()]

    async def get_review_batch(self, db: AsyncSession, scope: str, after_id: int = 0, limit: int = 30) -> list[dict]:
        """按 scope 取一批用于 AI 审核的标签。

        scope:
          - pending : status=0 待审核（默认，只审没审过的）
          - online  : status=1 已上线（重审已上线）
          - all     : status != 2 即除已忽略外全部（全部重审）
        """
        if scope == "online":
            sql = "SELECT id, name, status FROM tags WHERE status = 1 AND id > :after ORDER BY id LIMIT :limit"
        elif scope == "all":
            sql = "SELECT id, name, status FROM tags WHERE status != 2 AND id > :after ORDER BY id LIMIT :limit"
        else:
            sql = "SELECT id, name, status FROM tags WHERE status = 0 AND id > :after ORDER BY id LIMIT :limit"
        r = await db.execute(text(sql), {"after": after_id, "limit": limit})
        return [dict(row) for row in r.mappings().all()]

    async def demote_to_pending(self, db: AsyncSession, ids: list[int]) -> int:
        """重审时把 uncertain 的已上线标签降回待审核。"""
        if not ids:
            return 0
        r = await db.execute(
            text("UPDATE tags SET status = 0, updated_at = NOW() WHERE id = ANY(:ids)"),
            {"ids": ids},
        )
        await db.commit()
        return r.rowcount

    async def delete_by_ids(self, db: AsyncSession, ids: list[int]) -> int:
        if not ids:
            return 0
        r = await db.execute(
            text("DELETE FROM tags WHERE id = ANY(:ids)"),
            {"ids": ids},
        )
        await db.commit()
        return r.rowcount

    async def get_names_by_ids(self, db: AsyncSession, ids: list[int]) -> list[dict]:
        """按传入 ids 的顺序返回 [{id, name}]，跳过不存在的。"""
        if not ids:
            return []
        r = await db.execute(
            text("SELECT id, name FROM tags WHERE id = ANY(:ids)"),
            {"ids": ids},
        )
        rows = {row["id"]: row["name"] for row in r.mappings().all()}
        return [{"id": i, "name": rows[i]} for i in ids if i in rows]

    async def list_active(self, db: AsyncSession) -> list[dict]:
        r = await db.execute(
            text(
                "SELECT id, name, slug, color, description, article_count "
                "FROM tags WHERE status = 1 ORDER BY sort, id"
            )
        )
        return [dict(row) for row in r.mappings().all()]

    async def stats(self, db: AsyncSession) -> dict:
        r = await db.execute(
            text("SELECT status, COUNT(*) FROM tags GROUP BY status")
        )
        counts = {row[0]: row[1] for row in r.fetchall()}
        r2 = await db.execute(
            text("SELECT COUNT(*) FROM tags WHERE harvested = FALSE AND status != 2")
        )
        unharvested = r2.scalar() or 0
        return {
            "pending": counts.get(0, 0),
            "online": counts.get(1, 0),
            "ignored": counts.get(2, 0),
            "total": sum(counts.values()),
            "unharvested": unharvested,
        }


def _is_valid_keyword(kw: str) -> bool:
    """硬过滤明显不是 SEO keyword 的垃圾：URL、整句新闻、过长、有异常标点等。"""
    if len(kw) > 50 or len(kw) < 2:
        return False
    # URL / protocol / domain
    if re.search(r"https?://|://|www\.|\.com|\.cn|\.net|\.org", kw, re.I):
        return False
    # 新闻句常见标点（冒号、句号、感叹号、省略号）
    if re.search(r"[：:。！!？\?…]", kw):
        return False
    return True


def _to_slug(kw: str) -> str:
    slug = kw.lower().strip()
    slug = re.sub(r'[^\w\s-]', '', slug)
    slug = re.sub(r'[\s_]+', '-', slug)
    slug = re.sub(r'-+', '-', slug).strip('-')
    return slug[:50] if slug else "tag"


tag_logic = TagLogic()
