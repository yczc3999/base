"""
集中式路由注册表 — 唯一注册入口

`register_routes(app)` 是 App 唯一路由注册入口；`build_registry()`
显式聚合各分片 Manifest，禁止运行时 glob 扫描。

Base 的 Manifest 分片为 system/admin/client/public/web/extensions；
全部由本模块按固定顺序显式聚合。

下游扩展：
- 基座版本不修改 `build_registry()`。
- 下游在 `app/routes/extensions.py::register_extension_routes()`
  中显式调用自己的 Manifest registrar。
"""

from __future__ import annotations

from fastapi import FastAPI

from app.routes.admin import register_admin_routes
from app.routes.client import register_client_routes
from app.routes.extensions import register_extension_routes
from app.routes.public import register_public_routes
from app.routes.registry import RouteRegistry
from app.routes.system import register_system_routes
from app.routes.web import register_web_routes


def build_registry() -> RouteRegistry:
    """显式聚合所有 Base Manifest 分片。"""
    routes = RouteRegistry()

    register_system_routes(routes)
    register_public_routes(routes)
    register_admin_routes(routes)
    register_client_routes(routes)
    register_extension_routes(routes)
    register_web_routes(routes)
    return routes


def register_routes(app: FastAPI) -> None:
    """App 唯一路由注册入口。"""
    routes = build_registry()
    routes.validate()
    routes.install(app)


__all__ = ["RouteRegistry", "build_registry", "register_routes"]
