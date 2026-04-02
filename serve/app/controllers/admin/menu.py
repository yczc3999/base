from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.database import get_db
from app.utils.response import ok
from app.logics.menu import menu_logic
from app.controllers.base import crud_router
from app.deps import AuthInfo, require_admin, require_perms

router = APIRouter()

# CRUD
router.include_router(crud_router(
    "menu", menu_logic,
    tags=["admin-menu"],
    auth_dep=require_admin,
    perms_prefix="admin:menu",
))


@router.get("/menu/tree")
async def menu_tree(
    auth: AuthInfo = Depends(require_admin),
    _: None = Depends(require_perms("admin:menu:list")),
    db: AsyncSession = Depends(get_db),
):
    """获取完整菜单树（管理菜单用）"""
    tree = await menu_logic.get_tree(db)
    return ok(tree)
