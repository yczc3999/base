"""SEO 内容流水线（每小时）

职责：
1. 补 simhash 指纹（历史漏算的兜底）
2. 草稿池空时 → 从 tags 库挑上线标签作种子 → AI 生成文章 → 入草稿
   池有货时不主动生产，避免挤爆队列

自动发布的上游补给：
  empty draft pool → pipeline 自动生成 → scheduler 排期 → publisher 发布

受阶段配额约束（cold 阶段 collect_mult=0，跳过生产）。
"""
import logging

from sqlalchemy import text

from app.tasks.base import BaseTask
from app.services.database import async_session
from app.services.seo_phase import seo_phase_service
from app.services.seo_simhash import seo_simhash_service
from app.logics.publish_log import publish_log_logic
from app.logics.article_tag import article_tag_logic

logger = logging.getLogger("task")


class SeoPipelineTask(BaseTask):
    name = "SEO 内容流水线"
    interval = 3600  # 每小时

    # 草稿池低水位 —— 低于此数触发自动生产
    DRAFT_POOL_MIN = 5

    async def _count_drafts_available(self, db) -> int:
        """可排期的草稿数（status=0 未删除且未排队）"""
        r = await db.execute(text(
            "SELECT COUNT(*) FROM articles a "
            "WHERE a.status = 0 AND a.deleted_at IS NULL "
            "AND NOT EXISTS (SELECT 1 FROM publish_schedule s "
            "  WHERE s.article_id = a.id AND s.status IN (0, 4))"
        ))
        return int(r.scalar() or 0)

    async def _articles_needing_simhash(self, db, limit: int) -> list[dict]:
        r = await db.execute(text(
            "SELECT id, title, content FROM articles "
            "WHERE simhash IS NULL AND deleted_at IS NULL "
            "ORDER BY id DESC LIMIT :n"
        ), {"n": limit})
        return [dict(row) for row in r.mappings().all()]

    async def _pick_seed_tags(self, db, limit: int) -> list[dict]:
        """挑已上线 (status=1) 的标签作为种子，按 article_count 升序
        （先让"文章最少"的 tag 补货，保持 topic cluster 均衡）。
        """
        r = await db.execute(text(
            "SELECT id, name FROM tags "
            "WHERE status = 1 "
            "ORDER BY article_count ASC, RANDOM() "
            "LIMIT :n"
        ), {"n": limit})
        return [dict(row) for row in r.mappings().all()]

    async def _gen_article_for_tag(self, db, tag: dict) -> int | None:
        """围绕 tag 生成一篇文章入库，返回 article_id。
        调用方已在事务外，这里独立 commit。失败返 None，不阻塞其他 tag。
        """
        from app.services import ai_content
        from app.services.seo_simhash import simhash64
        try:
            result = await ai_content.gen_article_for_tag(tag["name"])
        except Exception as e:
            await publish_log_logic.write(
                db, action="collect", level="warn",
                msg=f"AI 生成失败 tag='{tag['name']}': {e!r}"[:500],
                payload={"tag_id": tag["id"], "tag_name": tag["name"], "error": repr(e)[:300]},
            )
            return None

        content = result.get("content") or ""
        if len(content) < 200:
            return None  # 产出太短直接丢

        sh = simhash64(content)
        sh_signed = sh - (1 << 64) if sh >= (1 << 63) else sh
        try:
            r = await db.execute(text(
                "INSERT INTO articles "
                "(title, slug, summary, content, source, ai_processed, status, sort, simhash) "
                "VALUES (:title, :slug, :summary, :content, 0, TRUE, 0, 0, :sh) "
                "ON CONFLICT (slug) DO NOTHING "
                "RETURNING id"
            ), {"title": result["title"][:200], "slug": result.get("slug") or None,
                "summary": result.get("summary", "")[:500], "content": content,
                "sh": sh_signed})
            await db.commit()
            article_id = r.scalar()
        except Exception as e:
            await db.rollback()
            await publish_log_logic.write(
                db, action="collect", level="warn",
                msg=f"入库失败 tag='{tag['name']}': {e!r}"[:500],
                payload={"tag_id": tag["id"], "error": repr(e)[:300]},
            )
            return None

        if article_id:
            # 绑 tag 关系
            await article_tag_logic.set_tags(db, article_id, [tag["id"]], primary_tag_id=tag["id"])
            await publish_log_logic.write(
                db, action="collect", level="info", article_id=article_id,
                msg=f"AI 自动生成草稿：{result['title'][:80]}（tag: {tag['name']}）",
                payload={"tag_id": tag["id"], "tag_name": tag["name"], "title": result["title"]},
            )
        return article_id

    async def run(self):
        from app.services.seo_phase import is_seo_enabled
        async with async_session() as db:
            if not await is_seo_enabled(db):
                return  # SEO 总开关关闭

            cur = await seo_phase_service.get_current(db)
            phase = cur["phase"]
            quota = cur["quota"]
            collect_mult = quota.get("collect_mult", 0)
            daily_cap = quota.get("daily", 0)

            # === 1. 补 simhash（历史脏数据）===
            updated_sh = 0
            for art in await self._articles_needing_simhash(db, limit=50):
                await seo_simhash_service.update_for_article(db, art["id"], art["content"])
                updated_sh += 1
            await db.commit()

            # === 2. 草稿池低水位 + 阶段允许 → 自动生产 ===
            produced = 0
            available = await self._count_drafts_available(db)
            target = collect_mult * max(daily_cap, 1)   # 本轮目标产量
            need = max(self.DRAFT_POOL_MIN - available, 0)
            to_produce = min(need, target) if target > 0 else 0

            if to_produce > 0:
                seeds = await self._pick_seed_tags(db, to_produce * 2)  # 多挑些防失败
                if not seeds:
                    await publish_log_logic.write(
                        db, action="collect", level="warn",
                        msg=f"池低水位 ({available} < {self.DRAFT_POOL_MIN}) 但无已上线 tag 作种子",
                        payload={"available": available, "phase": phase},
                    )
                    await db.commit()
                else:
                    for tag in seeds:
                        if produced >= to_produce:
                            break
                        aid = await self._gen_article_for_tag(db, tag)
                        if aid:
                            produced += 1

        logger.info(
            "[SEO pipeline] phase=%s simhash+%d drafts=%d/%d produced=%d/target=%d",
            phase, updated_sh, available, self.DRAFT_POOL_MIN, produced, to_produce,
        )
