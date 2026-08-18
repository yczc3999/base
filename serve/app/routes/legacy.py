"""
CRUD 兼容层 — 为旧下游生成 APIRouter

本文件是唯一可以为旧下游生成 `APIRouter` 的兼容文件。
Base 自身不再调用兼容 `crud_router()`；下游在过渡期可继续：

```python
from app.controllers.base import crud_router
router.include_router(crud_router("order", order_logic, perms_prefix="admin:order"))
```

实现：复用 `register_legacy_crud()` 生成 RouteSpec，再通过
`RouteRegistry.compile_http_router()` 编译为与旧实现完全一致的 APIRouter
（含 operationId、response_model、依赖注入）。

移除兼容层属于 MAJOR 变更，不在本版本执行。
"""

from __future__ import annotations

import warnings
from typing import Any, Callable

from fastapi import APIRouter

from app.logics.base import BaseLogic
from app.routes.registry import RouteRegistry
from app.routes.resources import register_legacy_crud
from app.routes.types import RouteAccess


def crud_router(
    prefix: str,
    logic: BaseLogic,
    tags: list[str] | None = None,
    need_auth: bool = True,
    no_auth: list[str] | None = None,
    auth_dep: Callable[..., Any] | None = None,
    perms_prefix: str = "",
    actions: dict[str, list[str]] | None = None,
) -> APIRouter:
    """兼容旧 `controllers.base.crud_router()` 的 APIRouter 生成器。

    发出 DeprecationWarning；返回的 APIRouter 与旧实现行为等价。
    """
    warnings.warn(
        "app.controllers.base.crud_router() is deprecated; migrate to the "
        "centralized route registry (app.routes.register_routes). "
        "This compatibility shim will be removed in a future MAJOR release.",
        DeprecationWarning,
        stacklevel=2,
    )

    # 兼容旧签名：prefix 是资源名（如 "user"），旧调用方再 include_router(prefix="/api/admin")。
    # 因此 legacy router 的路径是 /{resource}/getList 等（不含 scope 前缀）。
    if no_auth or not need_auth:
        # 存在公开 action 时无法用一个 Group access 表达，逐 action 由
        # register_legacy_crud 的 need_auth/no_auth 决定；Group access 取最宽松。
        access = RouteAccess.PUBLIC if not need_auth else RouteAccess.AUTHENTICATED
    else:
        # 全部受保护：auth_dep 决定适用范围
        if auth_dep is None:
            access = RouteAccess.AUTHENTICATED
        else:
            access = RouteAccess.ADMIN

    registry = RouteRegistry()
    # legacy APIRouter 不校验（旧 main.py 不经 Registry validate），
    # 但 access/中间件仍写入 spec 以便 catalog 使用。
    group = registry.group(
        prefix="",
        name="legacy.",
        access=access,
    )
    register_legacy_crud(
        group,
        prefix=prefix,
        logic=logic,
        tags=tags,
        need_auth=need_auth,
        no_auth=no_auth,
        auth_dep=auth_dep,
        perms_prefix=perms_prefix,
        actions=actions,
    )
    return registry.compile_http_router(include_fallback=False)