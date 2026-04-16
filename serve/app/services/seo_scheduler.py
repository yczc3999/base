"""SEO 排期服务 —— 给 ai_processed 的草稿派 scheduled_at

按当前阶段配额（《SEO 策略基准》§2）：
- 检查日/周已用配额，未达上限才排
- 在 hour_window 内随机选时段
- 与最后一次已排的时间至少间隔 min_gap_min（错峰）
- 加 jitter_min 偏移避开整点
- skip_probability 概率"今日不排"（防绝对节律）
- cold 阶段：不自动排期，全部 status=4 needs_review
- stable 阶段：10% 随机打 sampled_for_review

约束：worker 每日 02:30 跑一次，给"未来 7 天"派任务；
publisher 每 5 min 扫到点的发。

用法：
    from app.services.seo_scheduler import seo_scheduler_service
    n = await seo_scheduler_service.assign_pending(db)
"""
from __future__ import annotations

import os
import random
from datetime import datetime, timedelta, time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.seo_phase import seo_phase_service, quota_for_phase


class SeoSchedulerService:
    LOOKAHEAD_DAYS = 7  # 一次派 7 天

    def __init__(self):
        # 实例独立 RNG，避免多 worker 实例共享 Python 全局 PRNG 撞同样种子
        # → 同时刻派出完全相同的 scheduled_at
        self._rng = random.Random(os.urandom(8))

    async def _pending_drafts(self, db: AsyncSession, limit: int) -> list[dict]:
        """v2: 取所有还没排期的草稿（status=0 + scheduled_at IS NULL + 未删除）。
        scheduler 只在 articles 表上工作。
        """
        r = await db.execute(text(
            "SELECT id, title FROM articles "
            "WHERE status = 0 AND deleted_at IS NULL AND scheduled_at IS NULL "
            "ORDER BY created_at DESC, id DESC LIMIT :n"
        ), {"n": limit})
        return [dict(row) for row in r.mappings().all()]

    async def _last_scheduled_at(self, db: AsyncSession) -> datetime | None:
        """v2: 直接查 articles.scheduled_at + published_at 最大值。"""
        r = await db.execute(text(
            "SELECT MAX(GREATEST(COALESCE(scheduled_at, '1970-01-01'::timestamp), "
            "                    COALESCE(published_at, '1970-01-01'::timestamp))) "
            "FROM articles WHERE deleted_at IS NULL"
        ))
        return r.scalar()

    async def _used_today_and_week(self, db: AsyncSession) -> tuple[int, int]:
        """v2: 已排+已发的今日/本周计数。"""
        r = await db.execute(text(
            "SELECT "
            "  COUNT(*) FILTER (WHERE COALESCE(scheduled_at, published_at) >= date_trunc('day', NOW())  AND COALESCE(scheduled_at, published_at) < date_trunc('day', NOW()) + INTERVAL '1 day') AS today, "
            "  COUNT(*) FILTER (WHERE COALESCE(scheduled_at, published_at) >= date_trunc('week', NOW()) AND COALESCE(scheduled_at, published_at) < date_trunc('week', NOW()) + INTERVAL '7 days') AS week "
            "FROM articles WHERE deleted_at IS NULL "
            "AND (scheduled_at IS NOT NULL OR status = 1)"
        ))
        row = r.mappings().first()
        return int(row["today"] or 0), int(row["week"] or 0)

    def _pick_slot(
        self, anchor: datetime, hour_window: tuple[int, int],
        min_gap_min: int, max_gap_min: int, jitter_min: int,
    ) -> datetime:
        """从 anchor 起，找下一个落在 hour_window 内的时段。
        基础间隔 = random[min_gap, max_gap]，加 ±jitter 偏移避免整点。
        """
        gap = self._rng.randint(min_gap_min, max_gap_min)
        candidate = anchor + timedelta(minutes=gap)
        # 抖动：避开整点 ±jitter
        jitter = self._rng.randint(-jitter_min, jitter_min)
        candidate += timedelta(minutes=jitter)
        # 推进到 hour_window 内
        h_start, h_end = hour_window
        for _ in range(48):  # 最多滚 2 天
            if h_start <= candidate.hour < h_end:
                return candidate.replace(second=0, microsecond=0)
            # 超出窗口 → 推到下一个 h_start
            if candidate.hour >= h_end:
                candidate = (candidate + timedelta(days=1)).replace(
                    hour=h_start, minute=self._rng.randint(5, 55), second=0, microsecond=0
                )
            else:  # candidate.hour < h_start
                candidate = candidate.replace(
                    hour=h_start, minute=self._rng.randint(5, 55), second=0, microsecond=0
                )
        return candidate.replace(second=0, microsecond=0)

    async def assign_pending(self, db: AsyncSession) -> dict:
        """v2 主入口：直接 UPDATE articles.scheduled_at。

        cold 阶段：草稿不自动派 → 留 scheduled_at IS NULL，用户在文章列表手动 publish
        其他阶段：按配额派时间到 articles.scheduled_at
        """
        from app.logics.publish_log import publish_log_logic

        cur = await seo_phase_service.get_current(db)
        phase = cur["phase"]
        quota = cur["quota"]

        result = {"phase": phase, "scheduled": 0, "skipped": 0, "details": []}

        # cold 阶段不自动派 —— 草稿停在 status=0 + scheduled_at IS NULL，
        # 用户需要在 articles 列表手动设 status=1 发布
        if quota["needs_approve"]:
            await publish_log_logic.write(
                db, action="schedule", level="info",
                msg=f"cold 阶段不自动派排期，草稿等待人工 publish",
                payload={"phase": phase},
            )
            return result

        # 配额上限
        used_today, used_week = await self._used_today_and_week(db)
        remain_today = max(quota["daily"] - used_today, 0)
        remain_week = max(quota["weekly"] - used_week, 0)

        max_pick = min(self.LOOKAHEAD_DAYS * quota["daily"], remain_week)
        drafts = await self._pending_drafts(db, max_pick)
        if not drafts:
            return result

        last_at = await self._last_scheduled_at(db) or datetime.utcnow()
        if last_at < datetime.utcnow():
            last_at = datetime.utcnow()

        for d in drafts:
            if remain_today <= 0 and remain_week <= 0:
                break
            if self._rng.random() < quota["skip_prob"]:
                result["skipped"] += 1
                continue

            slot = self._pick_slot(
                last_at,
                quota["hour_window"], quota["min_gap_min"], quota["max_gap_min"], quota["jitter_min"],
            )
            r = await db.execute(text(
                "UPDATE articles SET scheduled_at = :slot, updated_at = NOW() "
                "WHERE id = :aid AND status = 0 AND scheduled_at IS NULL "
                "RETURNING id"
            ), {"aid": d["id"], "slot": slot})
            if not r.scalar():
                continue  # 已被别人改了
            result["scheduled"] += 1
            result["details"].append({"id": d["id"], "title": d["title"], "at": slot.isoformat()})
            last_at = slot
            if slot.date() == datetime.utcnow().date():
                remain_today -= 1
            remain_week -= 1

        await publish_log_logic.write(
            db, action="schedule", level="info",
            msg=f"phase={phase}, 排 {result['scheduled']}, skip {result['skipped']}",
            payload={"phase": phase, **{k: v for k, v in result.items() if k != "details"}},
        )
        await db.commit()
        return result


seo_scheduler_service = SeoSchedulerService()
