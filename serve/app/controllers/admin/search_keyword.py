"""SEO 关键词管理 — CRUD + SSE 采集 + AI 种子 + 批量审核

和 tag 的区别：
- search_keywords 是"候选池"（从 Suggest API 递归挖出来的原始词）
- tags 是"落地页集合"（审核通过后才变成 tag + 上线着陆页）
上线 = search_keyword.status=1 + 自动创建 tag
"""

from __future__ import annotations

import asyncio
import json
import logging
import random

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.controllers.base import crud_router
from app.deps import require_admin
from app.logics.search_keyword import search_keyword_logic
from app.services.database import get_db, async_session
from app.utils.response import ok

logger = logging.getLogger(__name__)
router = APIRouter()

router.include_router(
    crud_router("search_keyword", search_keyword_logic, tags=["admin-search-keyword"],
                auth_dep=require_admin, perms_prefix="admin:search_keyword")
)


# ---- DTO ----

class HarvestDto(BaseModel):
    seeds: list[str] = Field(..., max_length=50)
    engines: list[str] = Field(default=["google", "duckduckgo"], max_length=5)
    depth: int = Field(default=2, ge=1, le=3)
    max_per_level: int = Field(default=20, ge=1, le=100)
    max_total: int = Field(default=200, ge=1, le=1000)


class PollHarvestDto(BaseModel):
    batch_size: int = Field(default=20, ge=1, le=50)
    engines: list[str] = Field(default=["google", "duckduckgo"], max_length=5)
    max_total: int = Field(default=100, ge=1, le=500)


class BulkIdsDto(BaseModel):
    ids: list[int] = Field(..., max_length=500)


class AiSeedDto(BaseModel):
    topic: str = "海外华人回国加速 VPN 看视频"
    count: int = Field(default=20, ge=1, le=100)


# ---- SSE ----

def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/search_keyword/harvest-stream", tags=["admin-search-keyword"])
async def harvest_stream(dto: HarvestDto, _=Depends(require_admin)):
    """SSE 流式采集：每采到一批词实时推送。采集结果直接入 search_keywords（status=0 待审核）"""
    from app.services.keyword_harvester import suggest_google, suggest_duckduckgo, suggest_yandex

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

        if results:
            async with async_session() as db:
                r = await search_keyword_logic.bulk_import(db, results)
                total_imported = r["imported"]

        yield _sse({"type": "done", "total": len(results), "imported": total_imported})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/search_keyword/poll-harvest-stream", tags=["admin-search-keyword"])
async def poll_harvest_stream(dto: PollHarvestDto, _=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """SSE 轮询采集：从库里取 harvested=false 的词作种子，扩一层"""
    unharvested = await search_keyword_logic.get_unharvested(db, limit=dto.batch_size)
    if not unharvested:
        async def empty():
            yield _sse({"type": "done", "total": 0, "imported": 0, "msg": "没有待采集的关键词"})
        return StreamingResponse(empty(), media_type="text/event-stream")

    seeds = [row["keyword"] for row in unharvested]
    seed_ids = [row["id"] for row in unharvested]
    await search_keyword_logic.mark_harvested(db, seed_ids)

    harvest_dto = HarvestDto(seeds=seeds, engines=dto.engines, depth=1,
                             max_per_level=len(seeds), max_total=dto.max_total)
    return await harvest_stream(harvest_dto, _)


# ---- 批量审核 ----

@router.post("/search_keyword/bulk-approve", tags=["admin-search-keyword"])
async def bulk_approve(dto: BulkIdsDto, _=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    r = await search_keyword_logic.bulk_approve(db, dto.ids)
    return ok(r)


@router.post("/search_keyword/bulk-reject", tags=["admin-search-keyword"])
async def bulk_reject(dto: BulkIdsDto, _=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    count = await search_keyword_logic.bulk_reject(db, dto.ids)
    return ok({"rejected": count})


# ---- 统计 ----

@router.get("/search_keyword/stats", tags=["admin-search-keyword"])
async def stats(_=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return ok(await search_keyword_logic.stats(db))


# ---- AI 种子词 ----

@router.post("/search_keyword/ai-seeds", tags=["admin-search-keyword"])
async def ai_seeds(dto: AiSeedDto, _=Depends(require_admin)):
    from app.services import ai_content
    seeds = await ai_content.gen_seeds(dto.topic, dto.count)
    return ok({"seeds": seeds, "topic": dto.topic})
