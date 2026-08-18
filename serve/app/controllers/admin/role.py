from fastapi import Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.database import get_db
from app.utils.response import ok, fail
from app.logics.role import role_logic
from app.deps import AuthInfo, current_auth


class AssignMenusDto(BaseModel):
    role_id: int
    menu_ids: list[int]


async def role_menu_ids(
    role_id: int,
    db: AsyncSession = Depends(get_db),
):
    """获取角色已分配的菜单 ID 列表"""
    ids = await role_logic.get_menu_ids(db, role_id)
    return ok(ids)


async def assign_menus(
    dto: AssignMenusDto,
    db: AsyncSession = Depends(get_db),
):
    """给角色分配菜单权限"""
    await role_logic.assign_menus(db, dto.role_id, dto.menu_ids)
    return ok(msg="分配成功")
