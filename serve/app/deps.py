"""
全局依赖注入 — 鉴权

鉴权策略由 `app/routes/` Manifest 的 Group/Route middleware 权威声明，
并由 RouteRegistry 编译为 FastAPI `Depends`。Controller 不再自行决定
端点是否公开；需要读取身份的 Handler 通过 `current_auth` 读取已经执行的策略结果：

    # 需要登录（任何角色）
    admin.get("/info", info).middleware(require_auth)

    # 需要管理员登录
    admin.get("/info", info).middleware(require_admin)

    # 需要前端用户登录
    client.get("/info", info).middleware(require_client)

    # 不需要登录 — 不加 Depends，天然公开
    public.post("/login", login)

鉴权失败时抛出 BizError，由全局异常处理器统一返回：
    {"code": 401, "msg": "请登录", "data": null}
    {"code": 403, "msg": "无权限", "data": null}
"""

from fastapi import Request, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.logics.base import BizError
from app.utils.token import verify_token
from app.services.database import get_db


class AuthInfo:
    """
    鉴权结果对象

    注入到路由函数后，可直接访问当前登录用户信息：
        auth.user_id          → 用户 ID
        auth.scope            → 作用域（"admin" / "client"）
        auth.username         → 用户名
        auth.is_super_admin   → 是否超级管理员
        auth.access_token     → 当前 access_token（用于登出等操作）
        auth.extra            → Redis 中存储的完整 user_info
    """
    __slots__ = ("user_id", "scope", "username", "is_super_admin", "access_token", "extra")

    def __init__(self, user_id: int, scope: str, username: str, access_token: str, extra: dict = None):
        self.user_id = user_id
        self.scope = scope
        self.username = username
        self.is_super_admin = (extra or {}).get("is_super_admin", False)
        self.access_token = access_token
        self.extra = extra or {}


async def require_auth(request: Request) -> AuthInfo:
    """
    要求登录（任何 scope）

    从 Authorization 头提取 Bearer token，查 Redis 验证。
    验证成功后将 AuthInfo 写入 request.state.auth，供 current_auth 读取。
    验证失败抛出 BizError(401)。
    """
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise BizError("请登录", 401)

    token = auth_header[7:]
    user_info = await verify_token(token)
    if not user_info:
        raise BizError("请登录", 401)

    auth_info = AuthInfo(
        user_id=user_info["user_id"],
        scope=user_info.get("scope", "admin"),
        username=user_info.get("username", ""),
        access_token=token,
        extra=user_info,
    )
    request.state.auth = auth_info
    return auth_info


def current_auth(request: Request) -> AuthInfo:
    """
    从已执行的 Route middleware 获取 AuthInfo。

    只取 request.state.auth，不做 Redis/DB 鉴权。
    若 middleware 缺失则视为路由配置错误。
    """
    auth = getattr(request.state, "auth", None)
    if auth is None:
        raise RuntimeError("route auth middleware is missing")
    return auth


def current_auth_optional(request: Request):
    """
    兼容 CRUD Controller 专用：可选的 AuthInfo。

    已标记为 protected 的 action 若缺失 AuthInfo，由 CrudController
    内部以路由配置错误失败；public action 传 None。
    普通受保护 Handler 必须使用硬失败的 current_auth。
    """
    return getattr(request.state, "auth", None)


async def require_admin(auth: AuthInfo = Depends(require_auth)) -> AuthInfo:
    """
    要求管理员登录

    在 require_auth 基础上，额外校验 scope == "admin"。
    非管理员抛出 BizError(403)。
    """
    if auth.scope != "admin":
        raise BizError("无权限", 403)
    return auth


async def require_client(auth: AuthInfo = Depends(require_auth)) -> AuthInfo:
    """
    要求前端用户登录

    在 require_auth 基础上，额外校验 scope == "client"。
    非前端用户抛出 BizError(403)。
    """
    if auth.scope != "client":
        raise BizError("无权限", 403)
    return auth


def require_perms(*perms: str):
    """
    权限校验依赖工厂

    用法：
        Depends(require_perms("admin:user:list"))
        Depends(require_perms("admin:user:create", "admin:user:edit"))  # 满足任一即可

    流程：
        1. is_super_admin → 直接放行
        2. 查用户权限列表（走 Redis 缓存）
        3. 匹配任一 perm → 放行
        4. 不匹配 → 403
    """
    async def _check_perms(
        auth: AuthInfo = Depends(require_admin),
        db: AsyncSession = Depends(get_db),
    ):
        if auth.is_super_admin:
            return auth

        from app.logics.admin_user import admin_user_logic

        user_perms = await admin_user_logic.get_user_perms(db, auth.user_id)

        # 满足任一权限即可
        for p in perms:
            if p in user_perms:
                return auth

        raise BizError("无权限", 403)

    return _check_perms
