"""SEO 管理后台 controllers (v2 简化版)

砍掉的 v1 端点：
- crud_router(publish_schedule)  —— 表已删
- approve_review / cancel_schedule —— cold 阶段不再走审核队列

保留：
- dashboard / kill_switch / phase/recompute / sitemap / indexnow / run_now
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.controllers.base import crud_router
from app.deps import AuthInfo, require_admin
from app.services.database import get_db, async_session
from app.services.redis import get_redis
from app.services.seo_phase import seo_phase_service
from app.services.seo_sitemap import seo_sitemap_service
from app.services.seo_indexnow import indexnow_service
from app.logics.publish_log import publish_log_logic
from app.logics.article import article_logic
from app.utils.response import ok, fail

router = APIRouter()

# CRUD：publish_log（只读列表）
router.include_router(
    crud_router(
        "publish_log", publish_log_logic,
        tags=["admin-seo"],
        auth_dep=require_admin, perms_prefix="admin:seo",
    )
)


# ============== Dashboard ==============

@router.get("/seo/dashboard", tags=["admin-seo"])
async def dashboard(_=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    cur = await seo_phase_service.get_current(db)
    usage = await seo_phase_service.usage_today_and_week(db)
    article_stats = await article_logic.stats(db)
    errors = await publish_log_logic.recent_errors(db, hours=24, limit=10)

    from sqlalchemy import text as _text
    r = await db.execute(_text(
        "SELECT id, action, level, article_id, msg, created_at FROM publish_log "
        "ORDER BY id DESC LIMIT 20"
    ))
    recent_activity = [dict(row) for row in r.mappings().all()]

    try:
        r = await get_redis()
        ks_on = (await r.get("seo:kill_switch")) in (b"1", "1", b"true", "true")
        ks_until = await r.get("seo:kill_until")
        if isinstance(ks_until, bytes):
            ks_until = ks_until.decode()
    except Exception:
        ks_on = False
        ks_until = None

    sitemap_info: dict = {"files": 0, "urls": 0, "mtime": None}
    try:
        base = seo_sitemap_service.base_dir
        if base.exists():
            chunk_files = sorted(base.glob("sitemap-*.xml"))
            sitemap_info["files"] = len(chunk_files)
            total_urls = 0
            for p in chunk_files:
                try:
                    total_urls += p.read_text(encoding="utf-8").count("<url>")
                except Exception:
                    pass
            sitemap_info["urls"] = total_urls
            idx = base / "sitemap.xml"
            if idx.exists():
                sitemap_info["mtime"] = idx.stat().st_mtime
    except Exception:
        pass

    return ok({
        "phase":           cur,
        "usage":           usage,
        "article_stats":   article_stats,
        "recent_errors":   errors,
        "recent_activity": recent_activity,
        "kill_switch":     {"on": ks_on, "until": ks_until},
        "sitemap":         sitemap_info,
    })


# ============== 总开关 ==============

class EnableDto(BaseModel):
    enabled: bool


@router.post("/seo/toggle", tags=["admin-seo"])
async def toggle_seo(dto: EnableDto, _=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """主开关：control 4 个 worker task 是否运行 + run-now 是否生效。
    持久化到 settings.seo.enabled，重启不丢。"""
    from app.logics.setting import setting_logic
    await setting_logic.set(db, "seo", "enabled", "true" if dto.enabled else "false")
    await publish_log_logic.write(
        db, action="kill_switch", level="info" if dto.enabled else "warn",
        msg=f"SEO 总开关 {'开启' if dto.enabled else '关闭'}",
        payload={"enabled": dto.enabled},
    )
    return ok({"enabled": dto.enabled})


# ============== 紧急 override ==============

class KillSwitchDto(BaseModel):
    on: bool
    duration: str | None = "24h"


@router.post("/seo/kill-switch", tags=["admin-seo"])
async def set_kill_switch(dto: KillSwitchDto, _=Depends(require_admin)):
    r = await get_redis()
    if not dto.on:
        await r.delete("seo:kill_switch")
        await r.delete("seo:kill_until")
        async with async_session() as db:
            await publish_log_logic.write(
                db, action="kill_switch", level="info",
                msg="禁发开关 OFF（人工恢复）", payload={"on": False},
            )
        return ok({"on": False})

    ttl_map = {"24h": 86400, "week": 604800, "forever": None}
    ttl = ttl_map.get(dto.duration or "24h", 86400)
    if ttl is not None:
        await r.set("seo:kill_switch", "1", ex=ttl)
        from datetime import datetime, timezone, timedelta
        until = (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat(timespec="seconds")
        await r.set("seo:kill_until", until, ex=ttl)
    else:
        await r.set("seo:kill_switch", "1")
        await r.delete("seo:kill_until")

    async with async_session() as db:
        await publish_log_logic.write(
            db, action="kill_switch", level="warn",
            msg=f"禁发开关 ON（持续 {dto.duration}）",
            payload={"on": True, "duration": dto.duration, "ttl_sec": ttl},
        )
    return ok({"on": True, "duration": dto.duration, "ttl_sec": ttl})


# ============== sitemap ==============

@router.post("/seo/sitemap/rebuild", tags=["admin-seo"])
async def rebuild_sitemap(_=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    info = await seo_sitemap_service.rebuild(db)
    if "error" in info:
        return fail(info["error"])
    return ok(info)


@router.get("/seo/sitemap/files", tags=["admin-seo"])
async def list_sitemap_files(_=Depends(require_admin)):
    base = seo_sitemap_service.base_dir
    if not base.exists():
        return ok({"files": []})
    files = []
    for p in sorted(base.glob("sitemap*.xml")):
        st = p.stat()
        files.append({"name": p.name, "size_bytes": st.st_size, "mtime": st.st_mtime})
    return ok({"files": files, "base_dir": str(base)})


# ============== 阶段 + IndexNow ==============

@router.post("/seo/phase/recompute", tags=["admin-seo"])
async def recompute_phase(_=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return ok(await seo_phase_service.detect_and_persist(db))


class IndexNowTestDto(BaseModel):
    urls: list[str] = Field(..., max_length=10)


@router.post("/seo/indexnow/test", tags=["admin-seo"])
async def indexnow_test(dto: IndexNowTestDto, _=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    await indexnow_service.ping(db, dto.urls)
    if indexnow_service.failed:
        return fail(indexnow_service.error or "IndexNow 失败")
    return ok(indexnow_service.result)


# ============== 立即跑全链路 ==============

@router.post("/seo/run-now", tags=["admin-seo"])
async def run_pipeline_now(_=Depends(require_admin)):
    """v2: pipeline 补货 → scheduler 排期 → publisher 发到点（直接读 articles）。
    手动触发不受 settings.seo.enabled 限制（管理员明确意图）。
    但仍受 kill_switch 保护：禁发期间不强行发布。"""
    from app.tasks.seo_pipeline import SeoPipelineTask
    from app.services.seo_scheduler import seo_scheduler_service
    from app.services.seo_publish import seo_publish_service

    # 手动 run-now 短暂强开 enabled 跑一次（不持久化）
    from app.logics.setting import setting_logic
    async with async_session() as db:
        prev_enabled = await setting_logic.get(db, "seo", "enabled", "false")
        if prev_enabled != "true":
            await setting_logic.set(db, "seo", "enabled", "true")

    log = []
    try:
        await SeoPipelineTask().run()
        log.append({"step": "pipeline", "status": "ok"})
    except Exception as e:
        log.append({"step": "pipeline", "status": "error", "msg": repr(e)[:200]})

    try:
        async with async_session() as db:
            sr = await seo_scheduler_service.assign_pending(db)
        log.append({"step": "scheduler", "status": "ok", **sr})
    except Exception as e:
        log.append({"step": "scheduler", "status": "error", "msg": repr(e)[:200]})

    pub = {"processed": 0, "published": 0, "skipped": 0, "errored": 0}
    try:
        async with async_session() as db:
            from sqlalchemy import text as _text
            await db.execute(_text(
                "UPDATE articles SET scheduled_at = NOW() - INTERVAL '1 minute' "
                "WHERE status = 0 AND scheduled_at IS NOT NULL AND scheduled_at > NOW() "
                "AND deleted_at IS NULL"
            ))
            await db.commit()
            tasks = await article_logic.get_due_for_publish(db, limit=20)
            pub["processed"] = len(tasks)
            for t in tasks:
                res = await seo_publish_service.publish_one(db, t["id"], source="manual")
                s = res.get("status")
                if s == "published": pub["published"] += 1
                elif s == "skipped": pub["skipped"] += 1
                else: pub["errored"] += 1
        log.append({"step": "publisher", "status": "ok", **pub})
    except Exception as e:
        log.append({"step": "publisher", "status": "error", "msg": repr(e)[:200]})

    # 恢复原 enabled 状态（不要因为手动 run-now 改变持久状态）
    if prev_enabled != "true":
        async with async_session() as db:
            await setting_logic.set(db, "seo", "enabled", prev_enabled)

    return ok({"log": log})
