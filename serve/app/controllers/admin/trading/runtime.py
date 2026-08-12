"""V2 Trading 运行时只读端点（WP-00d2）。

薄 Controller：复用 lifespan 写入 `app.state.trading_runtime` 的最近安全快照，
不重做任何 health 探测逻辑。强制 `admin:monitor:list` 权限。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.config import settings
from app.deps import AuthInfo, require_perms
from app.services.runtime import safe_unready_snapshot
from app.utils.response import ok

router = APIRouter()

_perm_list = require_perms("admin:monitor:list")


@router.get("/trading/runtime")
async def trading_runtime(request: Request, auth: AuthInfo = Depends(_perm_list)):
    """返回 V2 runtime 最近一次安全健康快照；无 runtime 时固定四组件 unready。"""
    runtime = getattr(request.app.state, "trading_runtime", None)
    if runtime is None:
        return ok(safe_unready_snapshot(settings.ARTIFACT_DRIVER))
    snapshot = runtime.last_snapshot
    if snapshot is None:
        return ok(safe_unready_snapshot(settings.ARTIFACT_DRIVER))
    return ok(snapshot)
