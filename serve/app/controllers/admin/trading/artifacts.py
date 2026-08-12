"""Artifact metadata/content read endpoints（WP-07A Checkpoint B）。

- metadata 与 content 分离；content 提供单段 Range，单次最大 1 MiB。
- 正确返回 206/Content-Range/Accept-Ranges/ETag；多 Range/越界/无 Range/超限 fail closed。
- generic read 需 ``v2:artifact:read``；AI request/raw/parsed artifact 还需 ``v2:ai:artifact``。
- 不返回存储路径/bucket credential/签名 URL/request header/secret。
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Request, Response

from app.config import settings
from app.controllers.admin.trading.common import get_admin_repo
from app.deps import AuthInfo, require_all_perms
from app.services.artifact_store.contracts import ArtifactRef, build_locator
from app.services.artifact_store.factory import build_artifact_store
from app.services.database import get_db
from app.utils.response import fail, ok
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()

_MAX_RANGE_BYTES = 1024 * 1024  # 1 MiB
_RANGE_RE = re.compile(r"^bytes=(\d+)-(\d+)$")


def _parse_range(header: str | None, total: int) -> tuple[int, int] | None:
    """解析单段 Range；多 Range/格式不符/无 Range/越界/超限 → None（fail closed）。"""
    if not header:
        return None
    if "," in header:
        return None  # 多 Range 拒绝
    match = _RANGE_RE.match(header.strip())
    if not match:
        return None
    start = int(match.group(1))
    end = int(match.group(2))
    if start < 0 or end < start or end >= total:
        return None  # 越界
    if (end - start + 1) > _MAX_RANGE_BYTES:
        return None  # 超限
    return start, end


@router.get("/v2/artifacts/{content_hash}/metadata")
async def artifact_metadata(content_hash: str, session: AsyncSession = Depends(get_db),
                            auth: AuthInfo = Depends(require_all_perms("v2:artifact:read"))):
    if not re.fullmatch(r"[0-9a-f]{64}", content_hash):
        return fail("content_hash_invalid", 400)
    meta = await get_admin_repo().artifact_metadata(session, content_hash)
    if meta is None:
        return fail("not_found", 404)
    lineage = await get_admin_repo().artifact_lineage(session, content_hash)
    return ok({
        "content_hash": meta["content_hash"],
        "content_type": meta["content_type"],
        "content_length": meta["content_length"],
        "lineage": lineage,
        "stored_at": meta["stored_at"],
    })


@router.get("/v2/artifacts/{content_hash}/content")
async def artifact_content(content_hash: str, request: Request,
                           session: AsyncSession = Depends(get_db),
                           auth: AuthInfo = Depends(require_all_perms("v2:artifact:read"))):
    if not re.fullmatch(r"[0-9a-f]{64}", content_hash):
        return fail("content_hash_invalid", 400)
    # AI artifact 附加权限：v2:ai:artifact
    is_ai = await get_admin_repo().is_ai_artifact(session, content_hash)
    if is_ai:
        user = await _has_ai_artifact_perm(auth)
        if not user:
            return fail("无权限", 403)
    meta = await get_admin_repo().artifact_metadata(session, content_hash)
    if meta is None:
        return fail("not_found", 404)
    total = int(meta["content_length"])
    rng = _parse_range(request.headers.get("range"), total)
    if rng is None:
        # 无 Range / 多 Range / 越界 / 超限 → fail closed（416）
        return Response(
            status_code=416,
            headers={"Content-Range": f"bytes */{total}", "Accept-Ranges": "bytes"},
            content=b"",
        )
    start, end = rng
    ref = ArtifactRef(
        sha256=content_hash,
        original_size=total,
        stored_size=int(meta["stored_size"] or total),
        mime=meta["content_type"],
        compression=meta["compression"],
        storage_driver=meta["storage_driver"],
        storage_version=meta["storage_version"],
        locator=build_locator(content_hash, meta["compression"]),
    )
    store = build_artifact_store(settings)
    # ArtifactStore.get_range 是半开区间 [start, end)；HTTP Range 为闭区间 [start, end]
    data = store.get_range(ref, start, end + 1)
    return Response(
        content=data,
        status_code=206,
        media_type=meta["content_type"],
        headers={
            "Content-Range": f"bytes {start}-{end}/{total}",
            "Accept-Ranges": "bytes",
            "ETag": f'"{content_hash}"',
        },
    )


async def _has_ai_artifact_perm(auth: AuthInfo) -> bool:
    """校验当前用户（非超管）是否具备 v2:ai:artifact；超管直接通过。"""
    if getattr(auth, "is_super_admin", False):
        return True
    from app.services.database import async_session

    from app.logics.admin_user import admin_user_logic

    async with async_session() as session:
        perms = await admin_user_logic.get_user_perms(session, auth.user_id)
    return "v2:ai:artifact" in perms
