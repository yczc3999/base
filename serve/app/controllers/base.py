"""
CrudRouter — 旧版 CRUD 路由工厂（兼容层）

本文件只允许 re-export 兼容函数，不得自行构造 Router。
Base 自身不再调用 `crud_router()`；新路由必须经
`app.routes.register_routes(app)` 唯一入口注册。

下游在过渡期可继续：
    from app.controllers.base import crud_router
    router.include_router(crud_router("order", order_logic, perms_prefix="admin:order"))

兼容实现位于 `app.routes.legacy.crud_router()`，会发出 DeprecationWarning。
移除兼容层属于 MAJOR 变更，不在本版本执行。
"""

from __future__ import annotations

from app.routes.legacy import crud_router

__all__ = ["crud_router"]

DEFAULT_ACTIONS = {
    "read": ["getList", "getDetail"],
    "write": ["doEdit"],
    "delete": ["doDelete"],
    "export": ["doExport"],
}