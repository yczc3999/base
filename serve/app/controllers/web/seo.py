"""SEO 公开路由 —— sitemap.xml / sitemap-N.xml / robots.txt / {indexnow_key}.txt

无鉴权，给爬虫 / IndexNow 调用。
sitemap 文件由 worker 落盘到 server/storage/seo/，本路由直接读返回。
indexnow key 文件动态返回，避免 key 改了还要手动改文件。
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.database import get_db
from app.services.seo_sitemap import seo_sitemap_service
from app.logics.setting import setting_logic

router = APIRouter()

_KEY_FILE_RE = re.compile(r"^[a-f0-9]{16,128}\.txt$", re.I)


@router.get("/sitemap.xml")
async def sitemap_index():
    content, ct = seo_sitemap_service.read("sitemap.xml")
    if not content:
        return Response("not generated yet", status_code=404, media_type="text/plain")
    return Response(content, media_type=ct)


@router.get("/sitemap-{n}.xml")
async def sitemap_chunk(n: int):
    name = f"sitemap-{n}.xml"
    content, ct = seo_sitemap_service.read(name)
    if not content:
        return Response("not found", status_code=404, media_type="text/plain")
    return Response(content, media_type=ct)


@router.get("/robots.txt")
async def robots():
    content, ct = seo_sitemap_service.read("robots.txt")
    if not content:
        # 兜底：未生成 sitemap 时也给出最小 robots
        return Response(
            "User-agent: *\nDisallow: /admin\nDisallow: /api\n",
            media_type="text/plain; charset=utf-8",
        )
    return Response(content, media_type=ct)


@router.get("/{name}")
async def indexnow_key_file(name: str, db: AsyncSession = Depends(get_db)):
    """IndexNow 协议要求 https://{host}/{key}.txt 必须返回 key 字符串本身。
    动态匹配：name 形如 abcdef0123...txt 且与 settings.seo_creds.indexnow_key 一致。

    防刷：先正则严匹配（不命中直接 404 不查 DB），再走 Redis 5 分钟缓存读 key。
    """
    # 先正则筛掉绝大多数无效请求 —— 0 DB / 0 Redis 开销
    if not _KEY_FILE_RE.match(name):
        return Response("not found", status_code=404, media_type="text/plain")

    # Redis 缓存 expected_key 5 分钟
    from app.services.redis import get_redis
    cache_key = "seo:indexnow_key_cache"
    expected_key = ""
    try:
        r = await get_redis()
        cached = await r.get(cache_key)
        if cached is not None:
            expected_key = cached.decode() if isinstance(cached, bytes) else cached
        else:
            expected_key = (await setting_logic.get(db, "seo_creds", "indexnow_key", "") or "").strip()
            await r.set(cache_key, expected_key, ex=300)
    except Exception:
        expected_key = (await setting_logic.get(db, "seo_creds", "indexnow_key", "") or "").strip()

    if not expected_key:
        return Response("not found", status_code=404, media_type="text/plain")
    if name[:-4].lower() != expected_key.lower():
        return Response("not found", status_code=404, media_type="text/plain")
    return Response(expected_key, media_type="text/plain; charset=utf-8")
