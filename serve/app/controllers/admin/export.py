"""
导出管理 — 进度查询 + 文件下载

接口：
    GET  /export/progress?key=xxx    查询导出进度
    GET  /export/download?key=xxx    下载导出文件
"""

import os
from fastapi import Depends, Request
from fastapi.responses import FileResponse
from app.deps import AuthInfo, current_auth
from app.utils.response import ok, fail
from app.utils.export_helper import validate_export_key
from app.services.redis import get_redis


async def export_progress(request: Request, auth: AuthInfo = Depends(current_auth)):
    """查询导出进度"""
    key = request.query_params.get("key", "")
    if not key:
        return fail("缺少 key 参数")

    if not validate_export_key(key, auth.user_id):
        return fail("无权查看该导出任务")

    r = await get_redis()
    progress = await r.get(f"export@{key}")

    if progress is None:
        return fail("导出任务不存在")

    progress = int(progress)

    if progress == -1:
        await r.delete(f"export@{key}")
        return ok({"progress": 0, "status": 2})  # 2 = 失败

    if progress >= 100:
        return ok({"progress": 100, "status": 1})  # 1 = 完成

    return ok({"progress": progress, "status": 0})  # 0 = 进行中


async def export_download(request: Request, auth: AuthInfo = Depends(current_auth)):
    """下载导出文件"""
    key = request.query_params.get("key", "")
    if not key:
        return fail("缺少 key 参数")

    if not validate_export_key(key, auth.user_id):
        return fail("无权下载该文件")

    r = await get_redis()
    file_path = await r.get(f"export_file@{key}")

    if not file_path:
        return fail("导出文件不存在或已过期")

    if not os.path.isfile(file_path):
        await r.delete(f"export_file@{key}")
        return fail("导出文件不存在")

    # 清理 Redis key
    await r.delete(f"export_file@{key}", f"export@{key}")

    # 根据扩展名决定文件名和 MIME 类型
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".zip":
        media_type = "application/zip"
        filename = "export.zip"
    else:
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = "export.xlsx"

    return FileResponse(
        path=file_path,
        media_type=media_type,
        filename=filename,
    )
