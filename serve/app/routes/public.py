"""
公开数据字典路由 — /api/dict

`/api/dict/items`：DataDict 枚举项（DictTag 组件无 Auth 拉取），Public。
"""

from __future__ import annotations

from app.controllers import dict as dict_public
from app.routes.registry import RouteRegistry
from app.routes.types import RouteAccess


def register_public_routes(routes: RouteRegistry) -> None:
    public = routes.group(
        prefix="/api",
        name="public.",
        tags=["dict-public"],
        access=RouteAccess.PUBLIC,
    )
    public.get("/dict/items", dict_public.get_items).name("dict.items")