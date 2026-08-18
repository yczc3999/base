"""
下游路由注册扩展点 — Base 中为空

下游 fork/clone 只在该函数中显式调用自己的 Manifest registrar，
不得修改 `build_registry()`；它可在自己的 `routes/extensions/`
目录中按领域分片，但本函数必须显式列出 registrar，不使用运行时自动扫描。
"""

from __future__ import annotations

from app.routes.registry import RouteRegistry


def register_extension_routes(routes: RouteRegistry) -> None:
    """Base intentionally leaves this empty."""
    return None