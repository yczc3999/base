from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.database import get_db
from app.utils.response import ok
from app.logics.menu import menu_logic


async def menu_tree(
    db: AsyncSession = Depends(get_db),
):
    """获取完整菜单树（管理菜单用）"""
    tree = await menu_logic.get_tree(db)
    return ok(tree)
