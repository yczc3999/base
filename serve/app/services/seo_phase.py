"""SEO 阶段分类服务 —— cold / new / growing / stable

根据《SEO 策略基准》docs/一期/SEO-策略基准.md §1 实现。
任何阈值变更必须先更新文档再改本文。

输入：age_days（来自 settings.site.launched_at），published_cnt（来自 articles 表），
      indexed_cnt（来自 GSC API，Phase 2；MVP 阶段为 None）
输出：(phase, reason)，写入 settings.seo.current_phase / phase_reason

用法：
    from app.services.seo_phase import seo_phase_service
    phase, reason = await seo_phase_service.detect_and_persist(db)
    quota = seo_phase_service.quota_for_phase(phase, health_score)
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.logics.setting import setting_logic


# === 索引率基准曲线（IndexCheckr 16M 页研究拟合）===

def expected_index_rate(age_days: int) -> float:
    """期望的 indexed/published 比例（一个健康站点此站龄下应达到的索引率）。"""
    if age_days < 7:
        return 0.00
    if age_days < 14:
        return 0.14
    if age_days < 30:
        return 0.50
    if age_days < 90:
        return 0.65
    if age_days < 180:
        return 0.75
    return 0.85


def index_health_score(indexed: int | None, published: int, age_days: int) -> float | None:
    """actual / expected 比值；GSC 未接入时返 None。"""
    if indexed is None:
        return None
    actual = indexed / max(published, 1)
    expected = expected_index_rate(age_days)
    return round(actual / max(expected, 0.01), 2)


def classify_phase(
    age_days: int, published_cnt: int, indexed_cnt: int | None
) -> tuple[str, str]:
    """阶段分类决策树。返回 (phase, reason)。
    严格对齐《SEO 策略基准》§1.4。
    """
    # 冷启动：绝对新站 OR 内容极少
    if age_days < 30 or published_cnt < 3:
        return "cold", f"建站 {age_days} 天, 已发 {published_cnt} 篇 (冷启动阈 30 天/3 篇)"

    health = index_health_score(indexed_cnt, published_cnt, age_days)

    # 有 GSC 数据 → 用健康度精判
    if health is not None:
        if health < 0.4:
            return "new", f"索引健康度 {health} 异常 (<0.4), 强制降档"
        if age_days < 90:
            return "new", f"sandbox 期 (<90 天), 健康度 {health}"
        if age_days < 180 or health < 0.7:
            return "growing", f"成长期 / 健康度 {health} 未达稳态阈 0.7"
        if published_cnt >= 30 and health >= 0.7:
            return "stable", f"6 月+, 已发 ≥30 篇, 健康度 {health} ≥ 0.7"
        return "growing", "兜底"

    # 无 GSC → 退化为 age + published_cnt
    if age_days < 90:
        return "new", f"sandbox 期 (age={age_days}d, 无 GSC)"
    if age_days < 180:
        return "growing", f"成长期 (age={age_days}d, 无 GSC)"
    if published_cnt >= 30:
        return "stable", f"6 月+, 已发 {published_cnt} 篇 (无 GSC)"
    return "growing", f"age={age_days}d, published={published_cnt} (未达稳态发布量)"


# === 配额表（《SEO 策略基准》§2）===

_QUOTA_BASE = {
    "cold":    {"daily": 1, "weekly": 3,  "needs_approve": True,
                "hour_window": (10, 22), "min_gap_min": 480, "max_gap_min": 720,
                "jitter_min": 20, "skip_prob": 0.2,
                "collect_mult": 0, "indexnow": False, "sample_rate": 0.0},
    "new":     {"daily": 1, "weekly": 5,  "needs_approve": False,
                "hour_window": (10, 22), "min_gap_min": 360, "max_gap_min": 720,
                "jitter_min": 20, "skip_prob": 0.2,
                "collect_mult": 3, "indexnow": True, "sample_rate": 0.0},
    "growing": {"daily": 2, "weekly": 10, "needs_approve": False,
                "hour_window": (9, 23),  "min_gap_min": 240, "max_gap_min": 600,
                "jitter_min": 15, "skip_prob": 0.1,
                "collect_mult": 4, "indexnow": True, "sample_rate": 0.0},
    "stable":  {"daily": 2, "weekly": 12, "needs_approve": False,
                "hour_window": (8, 23),  "min_gap_min": 180, "max_gap_min": 540,
                "jitter_min": 15, "skip_prob": 0.1,
                "collect_mult": 5, "indexnow": True, "sample_rate": 0.10},
}

# 安全余量 — 任何浮动都不得突破
HARD_DAILY_CAP = 3
HARD_WEEKLY_CAP = 20


def quota_for_phase(phase: str, health_score: float | None = None) -> dict:
    """按阶段返回配额；health_score >= 1.0 时 daily +1 浮动奖励，仍受 HARD_DAILY_CAP 约束。"""
    q = dict(_QUOTA_BASE.get(phase, _QUOTA_BASE["new"]))
    if health_score is not None and health_score >= 1.0 and phase != "cold":
        q["daily"] = min(q["daily"] + 1, HARD_DAILY_CAP)
    return q


# === 业务服务：拉数据 → 算阶段 → 持久化 ===

class SeoPhaseService:
    async def _get_age_days(self, db: AsyncSession) -> int:
        launched = await setting_logic.get(db, "site", "launched_at", "")
        if not launched:
            return 0
        try:
            launched_d = date.fromisoformat(launched.strip()[:10])
        except (ValueError, TypeError):
            return 0
        return (date.today() - launched_d).days

    async def _get_published_cnt(self, db: AsyncSession) -> int:
        r = await db.execute(
            text(
                "SELECT COUNT(*) FROM articles "
                "WHERE status = 1 AND deleted_at IS NULL "
                "AND (published_at IS NULL OR published_at <= NOW())"
            )
        )
        return int(r.scalar() or 0)

    async def _get_indexed_cnt(self, db: AsyncSession) -> int | None:
        """读 settings.seo.indexed_cnt，由 GSC worker 定时刷新。MVP 阶段始终 None。"""
        v = await setting_logic.get(db, "seo", "indexed_cnt", "")
        if not v or not v.strip():
            return None
        try:
            return int(v)
        except (ValueError, TypeError):
            return None

    async def detect_and_persist(self, db: AsyncSession) -> dict:
        """完整流程：算阶段 → 写 settings → 返回结果 dict。
        调用方（worker / dashboard）拿返回值即可，不需要再读 settings。
        """
        age_days = await self._get_age_days(db)
        published_cnt = await self._get_published_cnt(db)
        indexed_cnt = await self._get_indexed_cnt(db)
        phase, reason = classify_phase(age_days, published_cnt, indexed_cnt)
        health = index_health_score(indexed_cnt, published_cnt, age_days)

        # 持久化只读字段
        await setting_logic.set(db, "seo", "current_phase", phase)
        await setting_logic.set(db, "seo", "phase_reason", reason)
        await setting_logic.set(db, "seo", "age_days", str(age_days))
        await setting_logic.set(db, "seo", "published_cnt", str(published_cnt))
        await setting_logic.set(db, "seo", "indexed_cnt", "" if indexed_cnt is None else str(indexed_cnt))
        await setting_logic.set(db, "seo", "health_score", "" if health is None else str(health))
        await setting_logic.set(db, "seo", "last_phase_check", datetime.utcnow().isoformat(timespec="seconds"))

        return {
            "phase": phase, "reason": reason,
            "age_days": age_days, "published_cnt": published_cnt,
            "indexed_cnt": indexed_cnt, "health_score": health,
            "quota": quota_for_phase(phase, health),
        }

    async def get_current(self, db: AsyncSession) -> dict:
        """快速读最近一次 detect 的缓存结果（不重新计算）。"""
        phase = await setting_logic.get(db, "seo", "current_phase", "cold")
        reason = await setting_logic.get(db, "seo", "phase_reason", "")
        age_days = int(await setting_logic.get(db, "seo", "age_days", "0") or 0)
        published = int(await setting_logic.get(db, "seo", "published_cnt", "0") or 0)
        indexed_raw = await setting_logic.get(db, "seo", "indexed_cnt", "")
        health_raw = await setting_logic.get(db, "seo", "health_score", "")
        last = await setting_logic.get(db, "seo", "last_phase_check", "")
        indexed = int(indexed_raw) if indexed_raw and indexed_raw.strip() else None
        health = float(health_raw) if health_raw and health_raw.strip() else None
        return {
            "phase": phase, "reason": reason,
            "age_days": age_days, "published_cnt": published,
            "indexed_cnt": indexed, "health_score": health,
            "last_check": last,
            "quota": quota_for_phase(phase, health),
        }

    async def usage_today_and_week(self, db: AsyncSession) -> dict:
        """已发数：今天 / 本周 (UTC)。v2: 直接查 articles。"""
        r = await db.execute(text(
            "SELECT "
            "  COUNT(*) FILTER (WHERE published_at >= date_trunc('day', NOW())) AS today, "
            "  COUNT(*) FILTER (WHERE published_at >= date_trunc('week', NOW())) AS this_week "
            "FROM articles WHERE status = 1 AND deleted_at IS NULL "
            "AND published_at IS NOT NULL"
        ))
        row = r.mappings().first()
        return {"today": int(row["today"] or 0), "this_week": int(row["this_week"] or 0)}


seo_phase_service = SeoPhaseService()


# === 全局开关辅助 ===

async def is_seo_enabled(db: AsyncSession) -> bool:
    """读 settings.seo.enabled。所有 worker task 跑前都该查一下。"""
    v = await setting_logic.get(db, "seo", "enabled", "false")
    return (v or "").strip().lower() == "true"
