"""Keyword 逻辑 — 合并原 tag / search_keyword / article_tag 三套逻辑.

生命周期:
    candidate (review_status=pending / ai_* / human_*)
        ↓ bulk_approve
    approved (stage=approved, review_status in human_approved/ai_approved)
        ↓ bulk_reject / demote_to_pending
    archived / candidate
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.logics.base import BaseLogic
from app.models.keyword import Keyword


class KeywordLogic(BaseLogic):
    model = Keyword
    cache_prefix = "keyword"
    except_keys = ["created_at", "updated_at"]

    def allowed_filters(self):
        return ["id", "stage", "review_status", "source_code"]

    def allowed_sorts(self):
        return ["id", "sort", "article_count", "fetched_at", "created_at"]

    def keyword_fields(self):
        return ["keyword", "slug", "seed_keyword"]

    # ---- 采集入库 ----

    async def bulk_import(self, db: AsyncSession, items: list[dict]) -> dict:
        """批量导入采集结果.

        items: [{"keyword": str, "source_code": str, "seed_keyword": str}, ...]
        ON CONFLICT (keyword_norm) DO UPDATE — 保留老记录, 更新 fetched_at/seed.
        """
        imported = 0
        skipped = 0
        now = datetime.utcnow()
        for item in items:
            kw = (item.get("keyword") or "").strip()
            if not kw or not _is_valid_keyword(kw):
                skipped += 1
                continue

            norm = _norm_keyword(kw)
            base_slug = _to_slug(kw)
            # 纯非 ASCII 时补 6 位 hash 防空/全撞
            if not base_slug or not any(c.isascii() and c.isalnum() for c in base_slug):
                base_slug = (base_slug + "-" if base_slug else "") + \
                    hashlib.md5(kw.encode()).hexdigest()[:6]

            r = await db.execute(
                text(
                    "INSERT INTO keywords "
                    "  (keyword, keyword_norm, source_code, seed_keyword, fetched_at) "
                    "VALUES (:kw, :norm, :src, :seed, :now) "
                    "ON CONFLICT (keyword_norm) DO UPDATE SET "
                    "  fetched_at = EXCLUDED.fetched_at, "
                    "  seed_keyword = COALESCE(EXCLUDED.seed_keyword, keywords.seed_keyword), "
                    "  updated_at = NOW() "
                    "RETURNING (xmax = 0) AS inserted"
                ),
                {
                    "kw": kw, "norm": norm,
                    "src": item.get("source_code", "manual"),
                    "seed": item.get("seed_keyword"),
                    "now": now,
                },
            )
            row = r.first()
            if row and row[0]:
                imported += 1
            else:
                skipped += 1
        await db.commit()
        return {"imported": imported, "skipped": skipped}

    # ---- 批量状态转换 ----

    async def bulk_approve(self, db: AsyncSession, ids: list[int]) -> int:
        """上线候选 → canonical (stage=approved + 自动 slug)."""
        if not ids:
            return 0
        r = await db.execute(
            text(
                "SELECT id, keyword, slug FROM keywords "
                "WHERE id = ANY(:ids) AND stage != 'approved'"
            ),
            {"ids": ids},
        )
        rows = r.mappings().all()
        for row in rows:
            slug = row["slug"] or _to_slug(row["keyword"])
            await db.execute(
                text(
                    "UPDATE keywords SET "
                    "  stage = 'approved', review_status = 'human_approved', "
                    "  slug = :slug, updated_at = NOW() "
                    "WHERE id = :id"
                ),
                {"slug": slug, "id": row["id"]},
            )
        await db.commit()
        return len(rows)

    async def bulk_reject(self, db: AsyncSession, ids: list[int]) -> int:
        """候选词人工驳回 → archived."""
        if not ids:
            return 0
        r = await db.execute(
            text(
                "UPDATE keywords SET "
                "  stage = 'archived', review_status = 'human_rejected', updated_at = NOW() "
                "WHERE id = ANY(:ids) AND stage = 'candidate'"
            ),
            {"ids": ids},
        )
        await db.commit()
        return r.rowcount

    async def demote_to_pending(self, db: AsyncSession, ids: list[int]) -> int:
        """重审时把不确定的已上线词降回候选."""
        if not ids:
            return 0
        r = await db.execute(
            text(
                "UPDATE keywords SET "
                "  stage = 'candidate', review_status = 'pending', updated_at = NOW() "
                "WHERE id = ANY(:ids)"
            ),
            {"ids": ids},
        )
        await db.commit()
        return r.rowcount

    async def delete_by_ids(self, db: AsyncSession, ids: list[int]) -> int:
        if not ids:
            return 0
        r = await db.execute(
            text("DELETE FROM keywords WHERE id = ANY(:ids)"),
            {"ids": ids},
        )
        await db.commit()
        return r.rowcount

    async def bulk_set_stage(self, db: AsyncSession, ids: list[int], stage: str) -> int:
        """自由改 stage (前端兼容老 bulk_set_status 接口用)."""
        if not ids or stage not in ("candidate", "approved", "archived"):
            return 0
        review_map = {
            "candidate": "pending",
            "approved": "human_approved",
            "archived": "human_rejected",
        }
        r = await db.execute(
            text(
                "UPDATE keywords SET "
                "  stage = :stage, review_status = :review, updated_at = NOW() "
                "WHERE id = ANY(:ids)"
            ),
            {"stage": stage, "review": review_map[stage], "ids": ids},
        )
        await db.commit()
        return r.rowcount

    # ---- 种子池 (轮询采集用) ----

    async def pick_seed_atomic(self, db: AsyncSession, limit: int = 1) -> list[dict]:
        """原子抢未扩展过的 candidate 种子. FOR UPDATE SKIP LOCKED 防并发抢同一条."""
        r = await db.execute(
            text(
                "WITH c AS ("
                "  SELECT id FROM keywords "
                "  WHERE expanded_as_seed_at IS NULL AND stage = 'candidate' "
                "  ORDER BY RANDOM() "
                "  FOR UPDATE SKIP LOCKED "
                "  LIMIT :n"
                ") "
                "UPDATE keywords k "
                "  SET expanded_as_seed_at = NOW(), updated_at = NOW() "
                "FROM c WHERE k.id = c.id "
                "RETURNING k.id, k.keyword"
            ),
            {"n": limit},
        )
        return [dict(row) for row in r.mappings().all()]

    # ---- 审核池 ----

    async def get_review_batch(
        self, db: AsyncSession, scope: str, after_id: int = 0, limit: int = 30,
    ) -> list[dict]:
        """按 scope 取一批待 AI 审核.

        scope:
          pending : stage=candidate & review_status=pending (默认)
          online  : stage=approved (重审已上线)
          all     : 除 archived 外全部
        """
        if scope == "online":
            sql = (
                "SELECT id, keyword, stage FROM keywords "
                "WHERE stage = 'approved' AND id > :after ORDER BY id LIMIT :limit"
            )
        elif scope == "all":
            sql = (
                "SELECT id, keyword, stage FROM keywords "
                "WHERE stage != 'archived' AND id > :after ORDER BY id LIMIT :limit"
            )
        else:
            sql = (
                "SELECT id, keyword, stage FROM keywords "
                "WHERE stage = 'candidate' AND review_status = 'pending' "
                "  AND id > :after ORDER BY id LIMIT :limit"
            )
        r = await db.execute(text(sql), {"after": after_id, "limit": limit})
        return [dict(row) for row in r.mappings().all()]

    # ---- 查询 ----

    async def get_names_by_ids(self, db: AsyncSession, ids: list[int]) -> list[dict]:
        """按传入顺序返回 [{id, keyword}]."""
        if not ids:
            return []
        r = await db.execute(
            text("SELECT id, keyword FROM keywords WHERE id = ANY(:ids)"),
            {"ids": ids},
        )
        rows = {row["id"]: row["keyword"] for row in r.mappings().all()}
        return [{"id": i, "keyword": rows[i]} for i in ids if i in rows]

    async def pick_seeds_by_fewest_articles(
        self, db: AsyncSession, limit: int = 10,
    ) -> list[dict]:
        """SEO pipeline 用: 挑已上线 keyword 作种子,按 article_count 升序 + RANDOM 破同."""
        r = await db.execute(
            text(
                "SELECT id, keyword FROM keywords "
                "WHERE stage = 'approved' "
                "ORDER BY article_count ASC, RANDOM() "
                "LIMIT :n"
            ),
            {"n": limit},
        )
        return [dict(row) for row in r.mappings().all()]

    async def list_active(self, db: AsyncSession) -> list[dict]:
        r = await db.execute(
            text(
                "SELECT id, keyword AS name, slug, color, description, article_count "
                "FROM keywords WHERE stage = 'approved' ORDER BY sort, id"
            ),
        )
        return [dict(row) for row in r.mappings().all()]

    async def stats(self, db: AsyncSession) -> dict:
        """聚合 stage 统计 + 未作为种子扩展过的候选词数."""
        r = await db.execute(
            text("SELECT stage, COUNT(*) FROM keywords GROUP BY stage"),
        )
        by_stage = {row[0]: row[1] for row in r.fetchall()}

        r2 = await db.execute(
            text(
                "SELECT COUNT(*) FROM keywords "
                "WHERE expanded_as_seed_at IS NULL AND stage = 'candidate'"
            ),
        )
        unharvested = r2.scalar() or 0

        return {
            "candidate": by_stage.get("candidate", 0),
            "approved":  by_stage.get("approved", 0),
            "archived":  by_stage.get("archived", 0),
            "total":     sum(by_stage.values()),
            "unharvested": unharvested,
        }


# ---- 辅助 ----

_NORM_WS = re.compile(r"\s+")


def _norm_keyword(kw: str) -> str:
    """去重归一化: lower + trim + 压缩连续空白."""
    return _NORM_WS.sub(" ", (kw or "").strip().lower())


def _is_valid_keyword(kw: str) -> bool:
    if len(kw) > 50 or len(kw) < 2:
        return False
    if re.search(r"https?://|://|www\.|\.com|\.cn|\.net|\.org", kw, re.I):
        return False
    if re.search(r"[:：。！!？\?…]", kw):
        return False
    return True


def _to_slug(kw: str) -> str:
    slug = kw.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:50] if slug else ""


keyword_logic = KeywordLogic()
