from fastapi import APIRouter, Request, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.database import get_db
from app.utils.response import ok, fail
from app.logics.role import role_logic
from app.controllers.base import crud_router
from app.deps import AuthInfo, require_admin, require_perms

router = APIRouter()

# CRUD
router.include_router(crud_router(
    "role", role_logic,
    tags=["admin-role"],
    auth_dep=require_admin,
    perms_prefix="admin:role",
))


class AssignMenusDto(BaseModel):
    role_id: int
    menu_ids: list[int]


@router.get("/role/menuIds")
async def role_menu_ids(
    role_id: int,
    auth: AuthInfo = Depends(require_admin),
    _: None = Depends(require_perms("admin:role:list")),
    db: AsyncSession = Depends(get_db),
):
    """获取角色已分配的菜单 ID 列表"""
    ids = await role_logic.get_menu_ids(db, role_id)
    return ok(ids)


@router.post("/role/assignMenus")
async def assign_menus(
    dto: AssignMenusDto,
    auth: AuthInfo = Depends(require_admin),
    _: None = Depends(require_perms("admin:role:assignMenu")),
    db: AsyncSession = Depends(get_db),
):
    """给角色分配菜单权限"""
    await role_logic.assign_menus(db, dto.role_id, dto.menu_ids)
    return ok(msg="分配成功")
