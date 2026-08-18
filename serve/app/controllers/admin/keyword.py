"""关键词管理 — CRUD + SSE 采集 + AI 审核 + 批量操作.

替代原 tag.py + search_keyword.py. 单表 keywords 用 stage 区分候选池/canonical.

URL 前缀: /api/admin/keyword/
"""
from __future__ import annotations

import asyncio
import json
import logging
import random

from fastapi import Body, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import current_auth
from app.logics.keyword import keyword_logic
from app.services.database import get_db, async_session
from app.services.keyword_harvester import ENGINE_MAP
from app.utils.response import ok

logger = logging.getLogger(__name__)
# ---- DTO ----

class HarvestDto(BaseModel):
    seeds: list[str] = Field(..., max_length=50)
    engines: list[str] = Field(default=["baidu", "google", "duckduckgo"], max_length=5)
    depth: int = Field(default=2, ge=1, le=3)
    max_per_level: int = Field(default=20, ge=1, le=100)
    max_total: int = Field(default=200, ge=1, le=1000)


class BulkIdsDto(BaseModel):
    ids: list[int] = Field(..., max_length=500)


class BulkStageDto(BaseModel):
    ids: list[int] = Field(..., max_length=500)
    stage: str = Field(...)  # candidate / approved / archived


class AiSeedDto(BaseModel):
    topic: str = "关键词种子"
    count: int = Field(default=20, ge=1, le=100)


class PollHarvestDto(BaseModel):
    batch_size: int = Field(default=20, ge=1, le=50)
    engines: list[str] = Field(default=["baidu", "google", "duckduckgo"], max_length=5)
    max_total: int = Field(default=5000, ge=1, le=50000)


# ---- SSE helper ----

def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# ---- SSE 单轮采集 ----

async def harvest_stream(dto: HarvestDto):
    """SSE 流式采集: 每采到一批词实时推送."""

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

                yield _sse({
                    "type": "seed", "seed": seed,
                    "index": i, "total_seeds": len(batch),
                })

                for engine_name in dto.engines:
                    if len(results) >= dto.max_total:
                        break
                    func_src = ENGINE_MAP.get(engine_name)
                    if not func_src:
                        continue
                    func, source_code = func_src

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
                        results.append({
                            "keyword": kw_clean,
                            "source_code": source_code,
                            "seed_keyword": seed,
                        })
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
                r = await keyword_logic.bulk_import(db, results)
                total_imported = r["imported"]

        yield _sse({"type": "done", "total": len(results), "imported": total_imported})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ---- SSE 轮询采集 (从库里 candidate 池持续抽种子) ----

async def poll_harvest_stream(dto: PollHarvestDto):
    """SSE 连续轮询: 从 keywords (candidate, expanded_as_seed_at IS NULL) 池子抽种子,
    调引擎扩散入库, 循环到池空或达 max_total.

    原子抢种子: FOR UPDATE SKIP LOCKED, 多 worker 并发安全.
    """

    async def event_stream():
        cumulative_found = 0
        cumulative_imported = 0
        round_no = 0

        while True:
            round_no += 1
            async with async_session() as db:
                picked = await keyword_logic.pick_seed_atomic(db, limit=dto.batch_size)
                await db.commit()
                if not picked:
                    break
                seeds = [row["keyword"] for row in picked]

            yield _sse({
                "type": "round_start", "round": round_no,
                "seeds_count": len(seeds), "seeds": seeds,
            })

            seen: set[str] = set()
            round_results: list[dict] = []

            for seed in seeds:
                if cumulative_found >= dto.max_total:
                    break
                yield _sse({"type": "seed", "seed": seed, "round": round_no})

                for engine_name in dto.engines:
                    if cumulative_found >= dto.max_total:
                        break
                    func_src = ENGINE_MAP.get(engine_name)
                    if not func_src:
                        continue
                    func, source_code = func_src

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
                        round_results.append({
                            "keyword": kw_clean,
                            "source_code": source_code,
                            "seed_keyword": seed,
                        })
                        new_in_round.append(kw_clean)
                        cumulative_found += 1

                    if new_in_round:
                        yield _sse({
                            "type": "found", "engine": engine_name, "seed": seed,
                            "keywords": new_in_round, "total": cumulative_found,
                            "round": round_no,
                        })
                    await asyncio.sleep(0.5)

            round_imported = 0
            if round_results:
                async with async_session() as db:
                    r = await keyword_logic.bulk_import(db, round_results)
                    round_imported = r["imported"]
                    cumulative_imported += round_imported

            yield _sse({
                "type": "round_done", "round": round_no,
                "found": len(round_results), "imported": round_imported,
                "cumulative_found": cumulative_found,
                "cumulative_imported": cumulative_imported,
            })

            if cumulative_found >= dto.max_total:
                break

        yield _sse({
            "type": "done", "rounds": max(round_no - 1, 0),
            "total": cumulative_found, "imported": cumulative_imported,
        })

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ---- 批量状态 ----

async def bulk_approve(
    dto: BulkIdsDto, db: AsyncSession = Depends(get_db),
):
    count = await keyword_logic.bulk_approve(db, dto.ids)
    return ok({"approved": count})


async def bulk_reject(
    dto: BulkIdsDto, db: AsyncSession = Depends(get_db),
):
    count = await keyword_logic.bulk_reject(db, dto.ids)
    return ok({"rejected": count})


async def bulk_set_stage(
    dto: BulkStageDto, db: AsyncSession = Depends(get_db),
):
    count = await keyword_logic.bulk_set_stage(db, dto.ids, dto.stage)
    return ok({"updated": count})


async def keyword_stats(db: AsyncSession = Depends(get_db)):
    return ok(await keyword_logic.stats(db))


# ---- AI 种子词 ----

async def ai_seed_suggest(dto: AiSeedDto):
    from app.services import ai_content
    seeds = await ai_content.gen_seeds(dto.topic, dto.count)
    return ok({"seeds": seeds, "topic": dto.topic})


# ---- AI 审核 (SSE 流式) ----

REVIEW_BATCH = 30


class AiReviewScopeDto(BaseModel):
    scope: str = Field(default="pending")  # pending | online | all


async def ai_review_stream(
    dto: AiReviewScopeDto = Body(default=AiReviewScopeDto()),
):
    """SSE AI 审核.

    scope:
      pending  — 只审 candidate + review_status=pending
      online   — 重审 approved (降级 uncertain 回 candidate)
      all      — 除 archived 外全部重审

    AI 三分类处理:
      approve   → bulk_approve (candidate → approved, 已上线 no-op)
      reject    → delete_by_ids
      uncertain → candidate 保 pending; approved 降回 candidate/pending
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
                batch = await keyword_logic.get_review_batch(
                    db, scope=scope, after_id=last_id, limit=REVIEW_BATCH,
                )

            if not batch:
                break

            last_id = batch[-1]["id"]
            keywords = [row["keyword"] for row in batch]
            id_map = {row["keyword"]: (row["id"], row.get("stage", "candidate")) for row in batch}

            yield _sse({
                "type": "batch_start",
                "batch_size": len(batch), "keywords": keywords,
            })

            try:
                items = await ai_content.review_tags(keywords)
                if not items:
                    yield _sse({"type": "error", "msg": "AI 返回格式异常"})
                    continue
            except Exception as e:
                yield _sse({"type": "error", "msg": str(e)})
                continue

            approve_ids, reject_ids, demote_ids = [], [], []
            approve_items, reject_items, uncertain_items = [], [], []

            for item in items:
                kw = item.get("keyword", "")
                decision = item.get("decision", "uncertain")
                info = id_map.get(kw)
                if not info:
                    continue
                kid, stage = info
                if decision == "approve":
                    approve_ids.append(kid)
                    approve_items.append({"id": kid, "keyword": kw})
                elif decision == "reject":
                    reject_ids.append(kid)
                    reject_items.append({"id": kid, "keyword": kw})
                else:
                    uncertain_items.append({"id": kid, "keyword": kw})
                    if stage == "approved":
                        demote_ids.append(kid)

            async with async_session() as db:
                if approve_ids:
                    await keyword_logic.bulk_approve(db, approve_ids)
                    approved_total += len(approve_ids)
                if reject_ids:
                    await keyword_logic.delete_by_ids(db, reject_ids)
                    rejected_total += len(reject_ids)
                if demote_ids:
                    await keyword_logic.demote_to_pending(db, demote_ids)
                    demoted_total += len(demote_ids)

            uncertain_total += len(uncertain_items)

            yield _sse({
                "type": "batch_done",
                "approve": approve_items, "reject": reject_items, "uncertain": uncertain_items,
                "cumulative": {
                    "approved": approved_total, "rejected": rejected_total,
                    "uncertain": uncertain_total, "demoted": demoted_total,
                },
            })

        yield _sse({
            "type": "done",
            "approved": approved_total, "rejected": rejected_total,
            "uncertain": uncertain_total, "demoted": demoted_total,
        })

    return StreamingResponse(event_stream(), media_type="text/event-stream")
