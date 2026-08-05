"""前端用户 (users) admin 端管理 — CRUD + 重置密码 + 踢下线.

users 的 scope 是 "client"。重置密码 / 踢下线都通过 revoke_all_tokens 撤销
该用户全部 Redis session (与 user_logic.modify 禁用即踢共用同一机制)。
"""
from fastapi import APIRouter, Request, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.controllers.base import crud_router
from app.deps import AuthInfo, require_admin, require_perms
from app.logics.user import user_logic
from app.services.database import get_db
from app.utils.response import ok, fail
from app.utils.token import revoke_all_tokens
from app.utils.validator import validate

router = APIRouter()

router.include_router(crud_router(
    "client_user", user_logic,
    tags=["admin-client-user"],
    auth_dep=require_admin,
    perms_prefix="admin:client_user",
))


@router.post("/client_user/resetPassword")
async def reset_password(
    request: Request,
    auth: AuthInfo = Depends(require_perms("admin:client_user:edit")),
    db: AsyncSession = Depends(get_db),
):
    """重置密码: 校验新密码 → hash 写入 → 强制该用户全部 session 下线."""
    body = await request.json()
    user_id = body.get("id")
    password = body.get("password")

    if not user_id:
        return fail("缺少用户 id")

    validate({"password": password}, {"password": "required|min:6"})

    user = await user_logic.get_detail(db, user_id)
    if not user:
        return fail("用户不存在")

    # save 走 before_edit → hash_password (password 非空路径)
    await user_logic.save(db, {"id": user_id, "password": password})
    await revoke_all_tokens("client", user_id)
    return ok(msg="密码已重置，该用户已强制下线")


@router.post("/client_user/kick")
async def kick(
    request: Request,
    auth: AuthInfo = Depends(require_perms("admin:client_user:edit")),
    db: AsyncSession = Depends(get_db),
):
    """踢下线: 撤销该用户全部 client session (仅登出, 不改账号)."""
    body = await request.json()
    user_id = body.get("id")
    if not user_id:
        return fail("缺少用户 id")

    user = await user_logic.get_detail(db, user_id)
    if not user:
        return fail("用户不存在")

    await revoke_all_tokens("client", user_id)
    return ok(msg="已将该用户踢下线")
