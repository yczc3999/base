"""Migration 管理 — 查看迁移状态 + 执行待执行迁移.

复用 app/migrate.py 的 get_status_list / run_migrations。
"""
from fastapi import APIRouter, Depends

from app.deps import AuthInfo, require_perms
from app.utils.response import ok, fail

router = APIRouter()

_perm_list = require_perms("admin:migration:list")
_perm_run = require_perms("admin:migration:run")


@router.get("/migration/list")
async def migration_list(auth: AuthInfo = Depends(_perm_list)):
    try:
        from app.migrate import get_status_list
        data = await get_status_list()
        return ok(data)
    except Exception as e:
        return fail(f"读取迁移状态失败: {e}")


@router.post("/migration/run")
async def migration_run(auth: AuthInfo = Depends(_perm_run)):
    # S3 修复: DDL 属极高危操作, 仅超管可执行
    if not auth.is_super_admin:
        return fail("仅超级管理员可执行迁移", 403)
    try:
        from app.migrate import run_migrations
        count = await run_migrations(verbose=False)
        return ok({"applied": count})
    except Exception as e:
        return fail(f"执行迁移失败: {e}")
