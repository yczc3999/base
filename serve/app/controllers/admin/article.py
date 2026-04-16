"""文章管理 — CRUD + AI 辅助 + 采集 + AI 批量润色

采集流程遵循设计哲学：
- Controller 只做路由和流控，不写 SQL
- Logic 管入库和查询
- Service 管第三方调用（搜索/抓取/AI）
- 采集和 AI 处理分两个独立步骤，每步可控可预览

时间字段约定（前台展示/排序一律用 published_at，created_at 仅作审计元数据）：
- published_at  ← 对外"发布时间"，可编辑（前端底栏可填任意过去/未来时间）；
                  status=1 且 published_at 为空时由 article_logic.before_create/before_edit
                  自动填 NOW()
- created_at    ← 入库时间，不可编辑
- updated_at    ← 更新时间，ORM 自动维护
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.controllers.base import crud_router
from app.deps import require_admin
from app.logics.article import article_logic
from app.services.database import get_db, async_session
from app.utils.response import ok

logger = logging.getLogger(__name__)
router = APIRouter()

router.include_router(
    crud_router("article", article_logic, tags=["admin-article"],
                auth_dep=require_admin, perms_prefix="admin:article")
)


# ---- DTO ----

class AiGenerateDto(BaseModel):
    type: str  # title / slug / summary / chat / gen_article
    title: str = ""
    content: str = ""
    summary: str = ""
    keyword: str = ""
    message: str = ""         # chat 专用：用户本轮输入
    prior_content: str = ""   # chat 专用：上次生成内容（由后端负责拼接，前端只透传）

class CollectDto(BaseModel):
    mode: str = "keyword"
    keyword: str = ""
    url: str = ""
    count: int = Field(default=5, ge=1, le=20)

class AiRewriteDto(BaseModel):
    ids: list[int] = Field(..., max_length=20)

class BulkIdsDto(BaseModel):
    ids: list[int] = Field(..., max_length=100)

class TagGenDto(BaseModel):
    tag_ids: list[int] = Field(..., max_length=50)


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# ---- AI 辅助 ----

@router.post("/article/ai-generate", tags=["admin-article"])
async def ai_generate(dto: AiGenerateDto, _=Depends(require_admin)):
    from app.services import ai_content

    try:
        if dto.type == "title":
            return ok({"result": await ai_content.gen_title(dto.content or dto.summary)})
        elif dto.type == "slug":
            return ok({"result": await ai_content.gen_slug(dto.title)})
        elif dto.type == "summary":
            return ok({"result": await ai_content.gen_summary(dto.content)})
        elif dto.type == "gen_article":
            return ok({"result": await ai_content.gen_article(dto.keyword)})
        elif dto.type == "chat":
            # 兼容旧调用：未传 message 时用 content
            msg = dto.message or dto.content
            return ok({"result": await ai_content.chat(msg, prior_content=dto.prior_content)})
    except Exception as e:
        logger.warning("ai-generate %s failed: %s", dto.type, e)
        return ok({"result": "", "error": str(e)[:200]})

    return ok({"result": ""})


# ---- 第一步：采集入库（SSE 流式）----

@router.post("/article/collect-stream", tags=["admin-article"])
async def collect_stream(dto: CollectDto, _=Depends(require_admin)):
    """搜索 → 逐页抓取 → 分析是否文章 → trafilatura 提取正文 → 入库为草稿

    入库后不做 AI 处理。管理员在列表里预览原始内容，再决定批量 AI 润色。
    """
    from app.services.article_collector import search, fetch_and_extract

    async def event_stream():
        saved = 0

        if dto.mode == "url":
            urls_to_fetch = [(dto.url, "")]
        else:
            yield _sse({"type": "search", "keyword": dto.keyword})
            results = await search(dto.keyword, count=dto.count * 3)
            if not results:
                yield _sse({"type": "error", "msg": "搜索无结果"})
                yield _sse({"type": "done", "saved": 0, "total": 0})
                return
            yield _sse({"type": "search_done", "found": len(results),
                        "results": [{"title": r.title, "url": r.url, "snippet": r.snippet} for r in results]})
            urls_to_fetch = [(r.url, r.title) for r in results]

        analyzed = 0
        for url, fallback_title in urls_to_fetch:
            if saved >= dto.count:
                break

            analyzed += 1
            yield _sse({"type": "analyzing", "url": url, "index": analyzed})

            extracted = await fetch_and_extract(url)

            if not extracted:
                yield _sse({"type": "skip", "url": url, "reason": "抓取失败"})
                await asyncio.sleep(0.5)
                continue

            if not extracted.is_article:
                yield _sse({"type": "skip", "url": url, "reason": f"非文章页（{extracted.word_count}字）"})
                await asyncio.sleep(0.5)
                continue

            yield _sse({
                "type": "extracted",
                "url": url,
                "title": extracted.title,
                "word_count": extracted.word_count,
                "author": extracted.author,
                "date": extracted.date,
                "preview": extracted.content[:200],
            })

            # 入库
            async with async_session() as db:
                article_id = await article_logic.import_collected(
                    db, extracted.title, extracted.content, url,
                    extracted.author, extracted.date,
                )
                if article_id is None:
                    yield _sse({"type": "skip", "url": url, "reason": "已采集过"})
                else:
                    await db.commit()
                    saved += 1
                    yield _sse({"type": "saved", "id": article_id, "title": extracted.title,
                                "word_count": extracted.word_count})

            await asyncio.sleep(1)

        yield _sse({"type": "done", "saved": saved, "total": analyzed})

    return StreamingResponse(event_stream(), media_type="text/event-stream")




# ---- 批量按标签生成文章（SSE 流式，后端驱动）----

@router.post("/article/gen-from-tags-stream", tags=["admin-article"])
async def gen_from_tags_stream(dto: TagGenDto, _=Depends(require_admin)):
    """SSE 流式批量生成：后端按 tag_ids 顺序逐个生成，实时推送每篇进度。

    前端零业务逻辑 — 只接收 start/generating/created/error/done 事件并渲染。
    """
    from app.services import ai_content
    from app.logics.keyword import keyword_logic
    from sqlalchemy import text as sql_text

    async def event_stream():
        async with async_session() as db:
            rows = await keyword_logic.get_names_by_ids(db, dto.tag_ids)
            # 适配老代码: 字段名 keyword → name
            tags = [{"id": r["id"], "name": r["keyword"]} for r in rows]

        total = len(tags)
        if not total:
            yield _sse({"type": "done", "created": 0, "total": 0, "msg": "未找到有效标签"})
            return

        yield _sse({"type": "start", "total": total})
        created = 0

        for idx, tag in enumerate(tags, start=1):
            tag_name = tag["name"]
            yield _sse({"type": "generating", "tag": tag_name, "index": idx, "total": total})

            try:
                result = await ai_content.gen_article_for_tag(tag_name)
            except Exception as e:
                logger.warning("gen from tag '%s' failed: %s", tag_name, e)
                yield _sse({"type": "error", "tag": tag_name, "msg": repr(e)[:200]})
                continue

            try:
                async with async_session() as db:
                    await db.execute(sql_text(
                        "INSERT INTO articles (title, slug, summary, content, source, ai_processed, status, sort) "
                        "VALUES (:title, :slug, :summary, :content, 0, TRUE, 0, 0)"
                    ), {"title": result["title"], "slug": result["slug"] or None,
                        "summary": result["summary"], "content": result["content"]})
                    await db.commit()
                created += 1
                yield _sse({"type": "created", "tag": tag_name, "title": result["title"], "index": idx, "total": total})
            except Exception as e:
                logger.exception("insert article for tag '%s' failed", tag_name)
                yield _sse({"type": "error", "tag": tag_name, "msg": repr(e)[:200]})

        yield _sse({"type": "done", "created": created, "total": total})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ---- 批量 AI 润色（SSE 流式，后端驱动）----

@router.post("/article/ai-rewrite-stream", tags=["admin-article"])
async def ai_rewrite_stream(dto: AiRewriteDto, _=Depends(require_admin)):
    """SSE 流式批量润色：后端按 ids 顺序逐篇润色，实时推送进度。

    事件：start / rewriting / done_one / error / done
    """
    from app.services import ai_content

    async def event_stream():
        total = len(dto.ids)
        yield _sse({"type": "start", "total": total})
        processed = 0

        for idx, article_id in enumerate(dto.ids, start=1):
            async with async_session() as db:
                row = await article_logic.get_with_raw(db, article_id)
            if not row:
                yield _sse({"type": "error", "id": article_id, "msg": "文章不存在"})
                continue

            title = row.get("title") or f"#{article_id}"
            yield _sse({"type": "rewriting", "id": article_id, "title": title, "index": idx, "total": total})

            try:
                result = await ai_content.rewrite_article(row["raw_content"] or row["content"])
            except Exception as e:
                logger.warning("rewrite %d failed: %s", article_id, e)
                yield _sse({"type": "error", "id": article_id, "title": title, "msg": repr(e)[:200]})
                continue

            try:
                async with async_session() as db:
                    await article_logic.update_ai_content(
                        db, article_id,
                        title=result["title"], content=result["content"],
                        summary=result["summary"], slug=result["slug"],
                    )
                    await db.commit()
                processed += 1
                yield _sse({
                    "type": "done_one", "id": article_id,
                    "new_title": result["title"], "word_count": len(result["content"]),
                    "index": idx, "total": total,
                })
            except Exception as e:
                logger.exception("save rewrite %d failed", article_id)
                yield _sse({"type": "error", "id": article_id, "title": title, "msg": repr(e)[:200]})

        yield _sse({"type": "done", "processed": processed, "total": total})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ---- 采集统计 ----

@router.get("/article/collect-stats", tags=["admin-article"])
async def collect_stats(_=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return ok(await article_logic.collect_stats(db))
