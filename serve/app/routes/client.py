"""
Client 路由 — /api/client

划分为 Public（login/register/refreshToken）与 Protected（require_client）两个 Group。
"""

from __future__ import annotations

from app.controllers.client import message as client_message
from app.controllers.client import user as client_user
from app.deps import require_client
from app.routes.registry import RouteRegistry
from app.routes.types import RouteAccess


def register_client_routes(routes: RouteRegistry) -> None:
    client_public = routes.group(
        prefix="/api/client",
        name="client.",
        access=RouteAccess.PUBLIC,
    )

    # ---- 公开 ----
    client_public.post("/user/login", client_user.login).name("user.login")
    client_public.post("/user/register", client_user.register).name("user.register")
    client_public.post("/user/refreshToken", client_user.refresh_token).name(
        "user.refreshToken"
    )

    # ---- 受保护 ----
    client = client_public.group(
        middleware=[require_client],
        access=RouteAccess.CLIENT,
    )
    client.get("/user/info", client_user.user_info).name("user.info")
    client.post("/user/logout", client_user.logout).name("user.logout")
    client.get("/message/list", client_message.message_list).name("message.list")
    client.get("/message/unread", client_message.message_unread).name("message.unread")
    client.post("/message/read", client_message.message_read).name("message.read")
    client.post("/message/readAll", client_message.message_read_all).name(
        "message.readAll"
    )