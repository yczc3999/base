"""
CRUD 路由生成契约 — register_legacy_crud()

把一个 `CrudController` 显式注册为当前五个契约端点：

- GET  {prefix}/getList     → get_list
- GET  {prefix}/getDetail   → get_detail
- POST {prefix}/doEdit      → do_edit
- POST {prefix}/doDelete    → do_delete
- POST {prefix}/doExport    → do_export

兼容参数：
- `need_auth` / `no_auth` / `auth_dep` / `actions` 在兼容期内保留原语义。
- `auth_dep` 在旧实现中注入到 Handler 签名；本实现中它编译为
  Route 的鉴权 middleware（Base 下全部为 require_admin），行为等价。
- `actions` 在当前实现中只合并到局部变量、未影响路由行为，
  兼容层不得擅自赋予新语义。

权限元数据：
- 显式 `permissions`（如 "admin:user"）展开为
  list/detail/create|edit/delete/export 五组。
- `perms_prefix` 与 `permissions` 同义（优先取非空者）。
- doEdit 的 create/edit 权限在 CrudController.do_edit 内部动态判断。

`only` / `except_` 在生成前计算，结果必须进全局目录。
"""

from __future__ import annotations

from typing import Any, Callable

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.controllers.crud import CrudController
from app.deps import current_auth_optional, require_admin, require_auth, require_client
from app.logics.base import BaseLogic
from app.services.database import get_db
from app.routes.types import RouteAccess

# 旧 crud_router 的五个契约方法：URL 名 → Handler 方法名
_CRUD_ACTIONS: tuple[tuple[str, str], ...] = (
    ("getList", "get_list"),
    ("getDetail", "get_detail"),
    ("doEdit", "do_edit"),
    ("doDelete", "do_delete"),
    ("doExport", "do_export"),
)


def _needs_auth(method_name: str, need_auth: bool, no_auth: list[str] | None) -> bool:
    if not need_auth:
        return False
    return method_name not in set(no_auth or [])


def _make_handler(
    controller: CrudController,
    impl_name: str,
    *,
    protected: bool,
) -> Callable[..., Any]:
    """为指定 CRUD action 生成 HTTP Handler。

    为确保 OpenAPI operationId 与旧 crud_router 完全一致，
    handler 的 `__name__` 必须为旧闭包函数名：
    - protected:  get_list / get_detail / do_edit / do_delete
    - public:     get_list_public / get_detail_public / ... 对应 do_*_public
    """
    handler_name = impl_name if protected else f"{impl_name}_public"

    async def handler(request: Request, db: AsyncSession = Depends(get_db)):
        auth = current_auth_optional(request)
        if protected and auth is None:
            raise RuntimeError("route auth middleware is missing")
        user_id = auth.user_id if auth else None
        is_super = auth.is_super_admin if auth else False
        return await getattr(controller, impl_name)(request, db, user_id, is_super)

    handler.__name__ = handler_name
    handler.__qualname__ = handler_name
    return handler


def _make_export_handler(
    controller: CrudController,
    *,
    protected: bool,
) -> Callable[..., Any]:
    handler_name = "do_export" if protected else "do_export_public"

    async def handler(request: Request):
        auth = current_auth_optional(request)
        if protected and auth is None:
            raise RuntimeError("route auth middleware is missing")
        user_id = auth.user_id if auth else None
        return await controller.do_export(request, user_id)

    handler.__name__ = handler_name
    handler.__qualname__ = handler_name
    return handler


def register_legacy_crud(
    group,
    prefix: str,
    logic: BaseLogic,
    *,
    name: str | None = None,
    tags: list[str] | None = None,
    need_auth: bool = True,
    no_auth: list[str] | None = None,
    auth_dep: Callable[..., Any] | None = None,
    perms_prefix: str = "",
    actions: dict[str, list[str]] | None = None,
    only: list[str] | None = None,
    except_: list[str] | None = None,
) -> CrudController:
    """把一个 CrudController 显式注册为当前五个契约端点。

    返回创建的 CrudController，供 Manifest 或测试直接调用 Handler。

    兼容语义：
    - `need_auth=True` 时，route-level middleware 使用 `auth_dep or require_auth`；
      Base 下传给本函数的 auth_dep 一律为 require_admin，等价于旧 Handler
      签名中的 `Depends(require_admin)`。
    - `need_auth=False` 或 `no_auth` 命中的 action 不注入鉴权 middleware
      （旧实现中这些 action 的 Handler 签名没有 Depends）。
    - `actions` 只合并到 CrudController 内部，不改变路由行为。
    """
    resource = prefix.strip("/")
    resource_name = (name or resource).strip(".")
    controller = CrudController(logic, perms_prefix=perms_prefix, actions=actions)

    selected: list[tuple[str, str]] = []
    for url_name, impl_name in _CRUD_ACTIONS:
        if only is not None and url_name not in set(only):
            continue
        if except_ is not None and url_name in set(except_):
            continue
        selected.append((url_name, impl_name))

    for url_name, impl_name in selected:
        protected = _needs_auth(url_name, need_auth, no_auth)
        policy = auth_dep or require_auth

        # 权限展开：list/detail/create|edit/delete/export
        perm_suffix = {
            "getList": "list",
            "getDetail": "detail",
            "doEdit": None,  # create|edit 动态判断，不预置静态权限
            "doDelete": "delete",
            "doExport": "export",
        }[url_name]

        route_id_suffix = f"{resource_name}.{url_name}"

        if url_name == "doExport":
            handler = _make_export_handler(controller, protected=protected)
        else:
            handler = _make_handler(controller, impl_name, protected=protected)

        if url_name in ("getList", "getDetail"):
            builder = group.get(f"/{resource}/{url_name}", handler)
        else:
            builder = group.post(f"/{resource}/{url_name}", handler)

        builder.name(route_id_suffix)
        if tags is not None:
            builder.tags(*tags)

        # response_model 契约（与旧 crud_router 完全一致）
        if url_name == "getList":
            from app.schemas.base import ListResponse

            builder.response_model(ListResponse)
        elif url_name == "getDetail":
            from app.schemas.base import DetailResponse

            builder.response_model(DetailResponse)

        # 鉴权与权限（等价旧 Handler 内的 Depends）：
        # - 带静态权限的 action（getList/getDetail/doDelete/doExport）只声明
        #   .permission()，编译为 require_perms（内部已含 require_admin）。
        # - doEdit 无静态权限（create/edit 在 CrudController.do_edit 内动态判断），
        #   只注入 auth_dep（Base 下为 require_admin）。
        # - 不重复叠加 require_admin：FastAPI 相同 callable 请求级 cache
        #   不作为长期重复声明的借口（设计文档 §5.2）。
        if protected:
            if policy is require_admin or (perms_prefix and perm_suffix):
                builder.access(RouteAccess.ADMIN)
            elif policy is require_client:
                builder.access(RouteAccess.CLIENT)
            else:
                builder.access(RouteAccess.AUTHENTICATED)
        else:
            # Legacy CRUD 允许同一资源有少量公开 action。公开 action 必须同时
            # 覆盖 access 并精确移除继承的 Group middleware，避免“元数据公开、
            # 运行时仍鉴权”的双重事实源。
            builder.access(RouteAccess.PUBLIC)
            if group.spec.middleware:
                builder.without_middleware(*group.spec.middleware)

        if perms_prefix and perm_suffix and protected:
            builder.permission(f"{perms_prefix}:{perm_suffix}")
        elif protected:
            builder.middleware(policy)

    return controller
