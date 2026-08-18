"""
系统路由 — /health* 与 /uploads Mount

- `/health` / `/health/live` / `/health/ready`：系统健康检查，Public。
- `/uploads`：public 存储静态文件挂载（路径计算与旧 main.py 完全一致）。

`storage_public_path()` 使用与旧 `main.py` 相同的路径计算；
`register_system_routes()` 在安装 MountSpec 前执行 mkdir。
"""

from __future__ import annotations

import os

from fastapi.staticfiles import StaticFiles

from app.controllers.health import health, health_live, health_ready
from app.routes.registry import RouteRegistry
from app.routes.types import RouteAccess


def storage_public_path() -> str:
    """public 存储目录（与旧 main.py 相同：serve/storage/public）。"""
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "storage",
        "public",
    )


def register_system_routes(routes: RouteRegistry) -> None:
    system = routes.group(prefix="", name="system.", access=RouteAccess.PUBLIC)

    system.get("/health", health).name("health")
    system.get("/health/live", health_live).name("health.live")
    system.get("/health/ready", health_ready).name("health.ready")

    storage_path = storage_public_path()
    os.makedirs(storage_path, exist_ok=True)
    system.mount(
        "/uploads",
        StaticFiles(directory=storage_path),
        name="uploads",
        access=RouteAccess.PUBLIC,
    )