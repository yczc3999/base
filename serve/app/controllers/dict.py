"""数据字典公开端点 — 无 auth, 供前端 DictTag / 业务页面拉取枚举项.

挂在根路径 `/api/dict/items` (非 admin 前缀), 与 admin 端 CRUD 分离。
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.logics.dict import dict_logic
from app.services.database import get_db
from app.utils.response import ok

router = APIRouter(prefix="/dict", tags=["dict-public"])


@router.get("/items")
async def get_items(type: str, db: AsyncSession = Depends(get_db)):
    """按 type_name 返回启用项 [{value, label}], 未知类型返回空数组."""
    data = await dict_logic.get_items_by_type(db, type)
    return ok(data)
