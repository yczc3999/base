"""SEO 发布编排服务（v2 简化版）

直接在 articles 表上工作，不再有 publish_schedule 表。
publish_one 接收 article_id 而不是 schedule_id。

调用方：
  - worker (publisher.run)：claim_due() 拿到 article ids → 逐个 publish_one
  - controller (run_now)：手动触发同样路径

失败重试：把 retry/scheduled_at 直接写在 articles 上。
单 worker 实例假设；多实例需要补 advisory_lock，目前不需要。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.logics.article import article_logic
from app.logics.article_keyword import article_keyword_logic
from app.logics.publish_log import publish_log_logic
from app.logics.setting import setting_logic
from app.services.seo_phase import seo_phase_service
from app.services.seo_simhash import seo_simhash_service
from app.services.seo_indexnow import indexnow_service
from app.services.seo_sitemap import seo_sitemap_service


INDEXNOW_COOLDOWN_SEC = 600  # 同 URL 10 分钟内不重复 ping
RETRY_BACKOFF_MIN = [5, 30, 180, 720, 1440]  # 5min, 30min, 3h, 12h, 24h
MAX_RETRY = 5


class SeoPublishService:
    # ---------- 质量闸 ----------

    async def _quality_gate(self, db: AsyncSession, article_id: int) -> tuple[bool, str | None, str | None]:
        r = await db.execute(text(
            "SELECT title, content, simhash, cover_image, ai_processed FROM articles "
            "WHERE id = :id AND deleted_at IS NULL"
        ), {"id": article_id})
        row = r.mappings().first()
        if not row:
            return False, "no_article", "文章不存在或已删除"

        require_ai = (await setting_logic.get(db, "seo", "quality_require_ai_processed", "true")) == "true"
        if require_ai and not row["ai_processed"]:
            return False, "no_ai", "未经 AI 润色"

        min_len = int(await setting_logic.get(db, "seo", "quality_min_content_length", "800") or 800)
        clen = len(row["content"] or "")
        if clen < min_len:
            return False, "thin_content", f"正文 {clen} < {min_len}"

        require_cover = (await setting_logic.get(db, "seo", "quality_require_cover_image", "false")) == "true"
        if require_cover and not (row["cover_image"] or "").strip():
            return False, "no_cover", "未配置封面图"

        # simhash 兜底自愈
        max_ham = int(await setting_logic.get(db, "seo", "quality_max_similarity_hamming", "10") or 10)
        if row["simhash"] is None:
            sh = await seo_simhash_service.update_for_article(db, article_id, row["content"] or "")
            await db.commit()
            await publish_log_logic.write(
                db, action="fingerprint", level="warn", article_id=article_id,
                msg="simhash 历史漏算，已现场补", payload={"simhash": sh},
            )
            current_sh = sh
        else:
            current_sh = int(row["simhash"])
        nearest = await seo_simhash_service.nearest_distance(db, current_sh, exclude_id=article_id)
        if nearest is not None and nearest < max_ham:
            return False, "similarity", f"hamming {nearest} < {max_ham}"

        # 同 keyword 7 天限次
        max_tag_repeat = int(await setting_logic.get(db, "seo", "quality_max_tag_repeat_7d", "2") or 2)
        kws = await article_keyword_logic.get_keywords_for_article(db, article_id)
        for k in kws:
            cnt = await article_keyword_logic.count_published_for_keyword_recent(db, k["id"], days=7)
            if cnt >= max_tag_repeat:
                return False, "tag_repeat", f"关键词『{k['name']}』7 天内已发 {cnt} 篇"

        return True, None, None

    # ---------- 状态切换 ----------

    async def _flip_to_published(self, db: AsyncSession, article_id: int) -> str | None:
        """status 0→1 + 清 retry/error + 写 published_at (如未填)"""
        r = await db.execute(text(
            "UPDATE articles SET "
            "  status = 1, "
            "  published_at = COALESCE(published_at, NOW()), "
            "  retry_count = 0, "
            "  last_publish_error = NULL, "
            "  updated_at = NOW() "
            "WHERE id = :id AND deleted_at IS NULL "
            "RETURNING slug"
        ), {"id": article_id})
        row = r.first()
        await db.commit()
        if not row or not row[0]:
            return None
        front = (await setting_logic.get(db, "site", "frontend_url", "") or "").rstrip("/")
        return f"{front}/article/{row[0]}" if front else None

    async def _mark_failed(self, db: AsyncSession, article_id: int, err: str) -> dict:
        """失败：retry+1，按退避表延后 scheduled_at；超 MAX_RETRY 转 skipped"""
        r = await db.execute(text(
            "SELECT retry_count FROM articles WHERE id = :id"
        ), {"id": article_id})
        cur = r.scalar() or 0
        if cur >= MAX_RETRY:
            await db.execute(text(
                "UPDATE articles SET "
                "  scheduled_at = NULL, "
                "  last_publish_error = :err, updated_at = NOW() "
                "WHERE id = :id"
            ), {"id": article_id, "err": f"超过最大重试 {MAX_RETRY} 次: {err[:300]}"})
            await db.commit()
            return {"action": "skipped_after_retries", "retry_count": cur}

        next_min = RETRY_BACKOFF_MIN[min(cur, len(RETRY_BACKOFF_MIN) - 1)]
        await db.execute(text(
            "UPDATE articles SET "
            "  retry_count = retry_count + 1, "
            "  scheduled_at = NOW() + (:m || ' minutes')::INTERVAL, "
            "  last_publish_error = :err, updated_at = NOW() "
            "WHERE id = :id"
        ), {"id": article_id, "m": str(next_min), "err": err[:1000]})
        await db.commit()
        return {"action": "rescheduled", "retry_count": cur + 1, "next_in_min": next_min}

    async def _mark_skipped(self, db: AsyncSession, article_id: int, skip_code: str, reason: str) -> None:
        """质量闸不通过 → 清 scheduled_at 让它退回草稿池等下次"""
        await db.execute(text(
            "UPDATE articles SET "
            "  scheduled_at = NULL, "
            "  last_publish_error = :reason, "
            "  updated_at = NOW() "
            "WHERE id = :id"
        ), {"id": article_id, "reason": f"[{skip_code}] {reason}"[:1000]})
        await db.commit()

    # ---------- IndexNow ----------

    async def _maybe_indexnow(self, db: AsyncSession, article_id: int, url: str) -> dict | None:
        cur = await seo_phase_service.get_current(db)
        if not cur["quota"].get("indexnow", False):
            return None
        last = await publish_log_logic.last_indexnow_at_for(db, article_id)
        if last is not None:
            elapsed = (datetime.utcnow() - last.replace(tzinfo=None)).total_seconds()
            if elapsed < INDEXNOW_COOLDOWN_SEC:
                return {"skipped": "cooldown", "elapsed_sec": int(elapsed)}
        await indexnow_service.ping(db, [url])
        if indexnow_service.failed:
            await publish_log_logic.write(
                db, action="indexnow", level="warn", article_id=article_id,
                msg=f"IndexNow ping 失败: {indexnow_service.error}",
                payload={"url": url, "error": indexnow_service.error},
            )
            return {"failed": indexnow_service.error}
        await publish_log_logic.write(
            db, action="indexnow", level="info", article_id=article_id,
            msg=f"IndexNow ping ok",
            payload={"url": url, **(indexnow_service.result or {})},
        )
        return indexnow_service.result

    # ---------- 主入口 ----------

    async def publish_one(
        self, db: AsyncSession, article_id: int, source: str = "auto",
    ) -> dict:
        """v2 主入口：直接发某篇文章。
        source: "auto" / "manual"
        """
        result: dict = {"article_id": article_id, "source": source}
        try:
            passed, skip_code, reason = await self._quality_gate(db, article_id)
            if not passed:
                await self._mark_skipped(db, article_id, skip_code, reason)
                await publish_log_logic.write(
                    db, action="skip", level="warn", article_id=article_id,
                    msg=f"skip ({source}): {reason}",
                    payload={"skip_code": skip_code, "source": source},
                )
                result["status"] = "skipped"
                result["skip_code"] = skip_code
                result["reason"] = reason
                return result

            url = await self._flip_to_published(db, article_id)
            await publish_log_logic.write(
                db, action="publish", level="info", article_id=article_id,
                msg=f"published ({source}): {url or '(no url)'}",
                payload={"url": url, "source": source},
            )
            result["status"] = "published"
            result["url"] = url

            if url:
                result["indexnow"] = await self._maybe_indexnow(db, article_id, url)

            sitemap_info = await seo_sitemap_service.rebuild(db)
            result["sitemap"] = {
                "files": len(sitemap_info.get("files", [])),
                "total_urls": sitemap_info.get("total_urls", 0),
            }
            return result

        except Exception as e:
            info = await self._mark_failed(db, article_id, str(e))
            await publish_log_logic.write(
                db, action="error", level="error", article_id=article_id,
                msg=f"publish failed ({source}): {e!r}",
                payload={"source": source, **info},
            )
            result["status"] = "error"
            result["error"] = str(e)
            return result


seo_publish_service = SeoPublishService()
