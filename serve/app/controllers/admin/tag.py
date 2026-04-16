"""标签管理 — CRUD + SSE 采集 + AI 审核 + 批量操作

标签 = SEO 关键词 = 着陆页。一张表一个页面走完全流程：
采集 → AI 初筛 → 人工审核 → 上线
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.controllers.base import crud_router
from app.deps import require_admin
from app.logics.tag import tag_logic
from app.services.database import get_db, async_session
from app.utils.response import ok

logger = logging.getLogger(__name__)
router = APIRouter()

router.include_router(
    crud_router("tag", tag_logic, tags=["admin-tag"],
                auth_dep=require_admin, perms_prefix="admin:tag")
)


# ---- DTO ----

class HarvestDto(BaseModel):
    seeds: list[str] = Field(..., max_length=50)
    engines: list[str] = Field(default=["google", "duckduckgo"], max_length=5)
    depth: int = Field(default=2, ge=1, le=3)
    max_per_level: int = Field(default=20, ge=1, le=100)
    max_total: int = Field(default=200, ge=1, le=1000)

class BulkIdsDto(BaseModel):
    ids: list[int] = Field(..., max_length=500)

class BulkStatusDto(BaseModel):
    ids: list[int] = Field(..., max_length=500)
    status: int

class AiSeedDto(BaseModel):
    topic: str = "海外华人回国加速 VPN 看视频"
    count: int = Field(default=20, ge=1, le=100)

class PollHarvestDto(BaseModel):
    batch_size: int = Field(default=20, ge=1, le=50)
    engines: list[str] = Field(default=["google", "duckduckgo"], max_length=5)
    max_total: int = Field(default=5000, ge=1, le=50000)

class AiReviewDto(BaseModel):
    limit: int = Field(default=30, ge=1, le=100)


# ---- SSE 采集（实时推送进度）----

def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/tag/harvest-stream", tags=["admin-tag"])
async def harvest_stream(dto: HarvestDto, _=Depends(require_admin)):
    """SSE 流式采集：每采到一批词实时推送到前端"""
    from app.services.keyword_harvester import suggest_google, suggest_duckduckgo, suggest_yandex
    import random

    engine_map = {
        "google": (suggest_google, 1),
        "duckduckgo": (suggest_duckduckgo, 3),
        "yandex": (suggest_yandex, 2),
    }

    async def event_stream():
        seen: set[str] = set()
        results: list[dict] = []
        current_seeds = list(dto.seeds)
        total_imported = 0

        for level in range(dto.depth):
            batch = current_seeds[:dto.max_per_level]
            next_seeds: list[str] = []

            yield _sse({"type": "level", "level": level, "seeds": len(batch)})

            for i, seed in enumerate(batch):
                if len(results) >= dto.max_total:
                    break
                seed_lower = seed.lower().strip()
                if seed_lower in seen:
                    continue
                seen.add(seed_lower)

                yield _sse({"type": "seed", "seed": seed, "index": i, "total_seeds": len(batch)})

                for engine_name in dto.engines:
                    if len(results) >= dto.max_total:
                        break
                    func, source = engine_map.get(engine_name, (None, 0))
                    if not func:
                        continue

                    suggestions = await func(seed)
                    new_in_round = []
                    for kw in suggestions:
                        if len(results) >= dto.max_total:
                            break
                        kw_clean = kw.strip()
                        kw_lower = kw_clean.lower()
                        if kw_lower in seen or not kw_clean or len(kw_clean) < 3:
                            continue
                        seen.add(kw_lower)
                        results.append({"keyword": kw_clean, "source": source, "seed_keyword": seed})
                        next_seeds.append(kw_clean)
                        new_in_round.append(kw_clean)

                    if new_in_round:
                        yield _sse({
                            "type": "found", "engine": engine_name, "seed": seed,
                            "keywords": new_in_round, "total": len(results),
                        })

                    await asyncio.sleep(0.5 + random.random() * 0.5)

            current_seeds = next_seeds
            if not current_seeds or len(results) >= dto.max_total:
                break

        # 入库
        if results:
            async with async_session() as db:
                r = await tag_logic.bulk_import(db, results)
                total_imported = r["imported"]

        yield _sse({"type": "done", "total": len(results), "imported": total_imported})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/tag/poll-harvest-stream", tags=["admin-tag"])
async def poll_harvest_stream(dto: PollHarvestDto, _=Depends(require_admin)):
    """SSE 连续轮询采集：一次调用里反复吃『库里 harvested=false 池』，
    每轮随机抽 batch_size 个作种子 → 调引擎扩 → 入库，循环到池空或累计达 max_total。
    """
    from app.services.keyword_harvester import suggest_google, suggest_duckduckgo, suggest_yandex

    engine_map = {
        "google": (suggest_google, 1),
        "duckduckgo": (suggest_duckduckgo, 3),
        "yandex": (suggest_yandex, 2),
    }

    async def event_stream():
        cumulative_found = 0
        cumulative_imported = 0
        round_no = 0

        while True:
            round_no += 1
            async with async_session() as db:
                unharvested = await tag_logic.get_unharvested(db, limit=dto.batch_size)
                if not unharvested:
                    break
                seed_ids = [row["id"] for row in unharvested]
                seeds = [row["name"] for row in unharvested]
                await tag_logic.mark_harvested(db, seed_ids)

            yield _sse({
                "type": "round_start", "round": round_no,
                "seeds_count": len(seeds), "seeds": seeds,
            })

            # --- 逐 seed 逐引擎扩散 ---
            seen: set[str] = set()
            round_results: list[dict] = []

            for seed in seeds:
                if cumulative_found >= dto.max_total:
                    break
                yield _sse({"type": "seed", "seed": seed, "round": round_no})

                for engine_name in dto.engines:
                    if cumulative_found >= dto.max_total:
                        break
                    func, source = engine_map.get(engine_name, (None, 0))
                    if not func:
                        continue
                    suggestions = await func(seed)
                    new_in_round = []
                    for kw in suggestions:
                        if cumulative_found >= dto.max_total:
                            break
                        kw_clean = kw.strip()
                        kw_lower = kw_clean.lower()
                        if kw_lower in seen or not kw_clean or len(kw_clean) < 3:
                            continue
                        seen.add(kw_lower)
                        round_results.append({"keyword": kw_clean, "source": source, "seed_keyword": seed})
                        new_in_round.append(kw_clean)
                        cumulative_found += 1

                    if new_in_round:
                        yield _sse({
                            "type": "found", "engine": engine_name, "seed": seed,
                            "keywords": new_in_round, "total": cumulative_found, "round": round_no,
                        })
                    await asyncio.sleep(0.5)

            # --- 本轮入库 ---
            round_imported = 0
            if round_results:
                async with async_session() as db:
                    r = await tag_logic.bulk_import(db, round_results)
                    round_imported = r["imported"]
                    cumulative_imported += round_imported

            yield _sse({
                "type": "round_done", "round": round_no,
                "found": len(round_results), "imported": round_imported,
                "cumulative_found": cumulative_found, "cumulative_imported": cumulative_imported,
            })

            if cumulative_found >= dto.max_total:
                break

        yield _sse({
            "type": "done",
            "rounds": round_no - (1 if round_no > 0 else 0),
            "total": cumulative_found,
            "imported": cumulative_imported,
        })

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ---- 批量审核 ----

@router.post("/tag/bulk-approve", tags=["admin-tag"])
async def bulk_approve(dto: BulkIdsDto, _=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    count = await tag_logic.bulk_approve(db, dto.ids)
    return ok({"approved": count})

@router.post("/tag/bulk-reject", tags=["admin-tag"])
async def bulk_reject(dto: BulkIdsDto, _=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    count = await tag_logic.bulk_reject(db, dto.ids)
    return ok({"rejected": count})

@router.post("/tag/bulk-status", tags=["admin-tag"])
async def bulk_set_status(dto: BulkStatusDto, _=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    count = await tag_logic.bulk_set_status(db, dto.ids, dto.status)
    return ok({"updated": count})


# ---- 统计 ----

@router.get("/tag/stats", tags=["admin-tag"])
async def tag_stats(_=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return ok(await tag_logic.stats(db))


# ---- AI 种子词 ----

@router.post("/tag/ai-seeds", tags=["admin-tag"])
async def ai_seed_suggest(dto: AiSeedDto, _=Depends(require_admin)):
    from app.services import ai_content
    seeds = await ai_content.gen_seeds(dto.topic, dto.count)
    return ok({"seeds": seeds, "topic": dto.topic})


# ---- AI 审核（SSE 流式）----

REVIEW_BATCH = 30  # 每批发给 AI 的数量


class AiReviewScopeDto(BaseModel):
    scope: str = Field(default="pending")  # pending | online | all


@router.post("/tag/ai-review-stream", tags=["admin-tag"])
async def ai_review_stream(dto: AiReviewScopeDto = Body(default=AiReviewScopeDto()),
                            _=Depends(require_admin)):
    """SSE 流式 AI 审核。

    scope:
      - pending : 只审 status=0 的（默认，最快，只审没审过的）
      - online  : 只重审 status=1 的（已上线的回头复查）
      - all     : 所有 status!=2 的都过一遍（全面重审）

    AI 三分类处理：
    - approve   → 调 bulk_approve（原本 pending 的上线；原本 online 的 no-op）
    - reject    → delete_by_ids（直接删除）
    - uncertain → 原 status=0 保持 pending；原 status=1 降回 pending 等人工复核
    """
    from app.services import ai_content

    scope = dto.scope if dto.scope in ("pending", "online", "all") else "pending"

    async def event_stream():
        approved_total = 0
        rejected_total = 0
        uncertain_total = 0
        demoted_total = 0
        last_id = 0

        yield _sse({"type": "scope", "scope": scope})

        while True:
            async with async_session() as db:
                batch = await tag_logic.get_review_batch(db, scope=scope, after_id=last_id, limit=REVIEW_BATCH)

            if not batch:
                break

            last_id = batch[-1]["id"]
            keywords = [row["name"] for row in batch]
            id_map = {row["name"]: (row["id"], row.get("status", 0)) for row in batch}

            yield _sse({"type": "batch_start", "batch_size": len(batch), "keywords": keywords})

            try:
                items = await ai_content.review_tags(keywords)
                if not items:
                    yield _sse({"type": "error", "msg": "AI 返回格式异常"})
                    continue
            except Exception as e:
                yield _sse({"type": "error", "msg": str(e)[:200]})
                continue

            approve_ids: list[int] = []
            reject_ids: list[int] = []
            demote_ids: list[int] = []  # status=1 但 uncertain → 降回 pending
            batch_results = []

            for item in items:
                kw = item.get("keyword", "")
                decision = item.get("decision", "uncertain")
                entry = id_map.get(kw)
                if not entry:
                    continue
                tag_id, cur_status = entry
                batch_results.append({"keyword": kw, "decision": decision, "was": cur_status})
                if decision == "approve":
                    approve_ids.append(tag_id)
                elif decision == "reject":
                    reject_ids.append(tag_id)
                elif decision == "uncertain" and cur_status == 1:
                    demote_ids.append(tag_id)

            async with async_session() as db:
                if approve_ids:
                    await tag_logic.bulk_approve(db, approve_ids)
                if reject_ids:
                    await tag_logic.delete_by_ids(db, reject_ids)
                if demote_ids:
                    await tag_logic.demote_to_pending(db, demote_ids)

            batch_approved = len(approve_ids)
            batch_rejected = len(reject_ids)
            batch_demoted = len(demote_ids)
            batch_uncertain = len(batch_results) - batch_approved - batch_rejected
            approved_total += batch_approved
            rejected_total += batch_rejected
            demoted_total += batch_demoted
            uncertain_total += batch_uncertain

            yield _sse({
                "type": "batch_done",
                "results": batch_results,
                "approved": batch_approved,
                "rejected": batch_rejected,
                "uncertain": batch_uncertain,
                "demoted": batch_demoted,
            })

        yield _sse({
            "type": "done",
            "scope": scope,
            "approved_total": approved_total,
            "rejected_total": rejected_total,
            "uncertain_total": uncertain_total,
            "demoted_total": demoted_total,
        })

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ---- AI 连接测试 ----

@router.post("/tag/ai-test", tags=["admin-tag"])
async def ai_test(_=Depends(require_admin)):
    from app.services import ai_content
    try:
        resp = await ai_content.test_connection()
        return ok({"message": f"连接正常，AI 回复：{resp.strip()[:50]}"})
    except Exception as e:
        from app.utils.response import fail
        return fail(f"连接失败：{e}")


