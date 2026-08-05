"""数据库备份 admin 端 — CRUD + 手动备份 + 下载.

恢复功能未实现（用户决策: 只做备份不做恢复）。
"""
from fastapi import APIRouter, Request, Depends
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.controllers.base import crud_router
from app.deps import AuthInfo, require_admin, require_perms
from app.logics.db_backup import db_backup_logic
from app.logics.base import BizError
from app.services.database import get_db
from app.utils.response import ok, fail

router = APIRouter()

router.include_router(crud_router(
    "db_backup", db_backup_logic,
    tags=["admin-db-backup"],
    auth_dep=require_admin,
    perms_prefix="admin:db_backup",
))


@router.post("/db_backup/backup")
async def manual_backup(
    auth: AuthInfo = Depends(require_perms("admin:db_backup:create")),
    db: AsyncSession = Depends(get_db),
):
    """手动触发一次备份（同步执行, 等待完成）."""
    try:
        result = await db_backup_logic.do_backup(db)
        return ok(result)
    except BizError as e:
        return fail(e.msg, e.code)


@router.get("/db_backup/download")
async def download_backup(
    request: Request,
    auth: AuthInfo = Depends(require_perms("admin:db_backup:list")),
    db: AsyncSession = Depends(get_db),
):
    """下载备份文件（校验 DB 记录 + 文件存在）."""
    filename = request.query_params.get("filename", "")
    if not filename:
        return fail("缺少 filename 参数")
    try:
        path = await db_backup_logic.get_download(db, filename)
    except BizError as e:
        return fail(e.msg, e.code)
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=filename,
    )
