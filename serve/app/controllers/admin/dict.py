"""数据字典 admin 端 — dicts CRUD + dict_items 子表 CRUD.

权限点统一挂在 `admin:dict:*` 下 (dict_item 是 dict 的子实体, 不单开权限)。
DictTag 组件走无 auth 的公开端点 `/api/dict/items` (见 controllers/dict.py)。
"""
from fastapi import APIRouter

from app.controllers.base import crud_router
from app.deps import require_admin
from app.logics.dict import dict_logic, dict_item_logic

router = APIRouter()

# dicts CRUD
router.include_router(crud_router(
    "dict", dict_logic,
    tags=["admin-dict"],
    auth_dep=require_admin,
    perms_prefix="admin:dict",
))

# dict_items 子表 CRUD (前端按 dict_id 过滤)
router.include_router(crud_router(
    "dict_item", dict_item_logic,
    tags=["admin-dict"],
    auth_dep=require_admin,
    perms_prefix="admin:dict",
))
