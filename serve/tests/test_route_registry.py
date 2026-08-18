"""
阶段 1：RouteRegistry / DSL 单元测试

覆盖设计文档第 4 节 DSL 精确语义 + 第 10 节编译阶段强制校验：

- Group 属性继承（prefix/name/tags/middleware/access）
- prefix 规范化连接（/ 开头、无尾斜杠、拒绝 // . ..）
- middleware 外层→内层→Route 顺序，精确相同 callable 去重
- Route ID 拼接与重复失败
- (Method, path) 碰撞
- fallback 编译顺序与参数化校验
- any() 在 /api/* 下 OPTIONS override 保护
- match() 拒绝空/重复/未知 Method
- path 参数与 Handler 签名一致性
- .permission() 必须 access=ADMIN
- catalog 稳定排序 + 固定 key
- install 顺序：普通 HTTP → mount → fallback
- admin/client access 边界
- operationId / mount 重复
- 未知 FastAPI route option
- without_middleware 禁止清空
"""

import asyncio
import json

import pytest

from app.deps import require_admin, require_auth, require_client
from app.routes.registry import RouteRegistry
from app.routes.types import (
    RouteAccess,
    RoutePriority,
    join_prefix,
)
from app.routes.validation import (
    RegistryValidationError,
    _dynamic_shadows_static,
)


async def _noop_handler():
    return {"ok": True}


async def _admin_handler():
    return {"ok": True}


async def _name_handler(name: str):
    return {"ok": True, "name": name}


async def _param_handler(x: str):
    return {"ok": True, "x": x}


# ==================== join_prefix ====================

def test_join_prefix_normalizes():
    assert join_prefix("/api", "admin", "user") == "/api/admin/user"
    assert join_prefix("api", "admin/") == "/api/admin"
    assert join_prefix("", "") == "/"
    assert join_prefix("/api/", "/admin/") == "/api/admin"
    assert join_prefix("/") == "/"


def test_join_prefix_rejects_dot_segments():
    with pytest.raises(ValueError):
        join_prefix("/api", "..")
    with pytest.raises(ValueError):
        join_prefix("/api", ".")


def test_join_prefix_rejects_double_slash():
    with pytest.raises(ValueError):
        join_prefix("/api//admin")
    # 分片边界的尾/首斜杠仍正常规范化。
    assert join_prefix("/api/", "/admin") == "/api/admin"


# ==================== Group 继承 ====================

def test_group_prefix_and_name_inheritance():
    routes = RouteRegistry()
    base = routes.group(prefix="/api/admin", name="admin.", access=RouteAccess.ADMIN)
    sub = base.group(prefix="/user", name="user.", tags=["admin-user"], middleware=[require_admin])

    sub.get("/info", _noop_handler).name("info")

    spec = routes.specs()[0]
    assert spec.path == "/api/admin/user/info"
    assert spec.route_id == "admin.user.info"
    assert spec.tags == ("admin-user",)
    assert spec.access is RouteAccess.ADMIN
    # middleware 从外层（空）→ 内层（require_admin）
    assert spec.middleware == (require_admin,)


def test_group_middleware_dedup_preserves_order():
    routes = RouteRegistry()
    mid = require_admin

    def _auth2():
        pass

    base = routes.group(
        prefix="/api/admin",
        name="admin.",
        middleware=[mid, _auth2, mid],
        access=RouteAccess.ADMIN,
    )
    base.get("/x", _admin_handler).name("x")
    spec = routes.specs()[0]
    # 精确相同 callable 去重，保留首次出现顺序
    assert spec.middleware == (mid, _auth2)


def test_nested_group_middleware_inherits_outer_then_inner():
    routes = RouteRegistry()

    def outer():
        pass

    def inner():
        pass

    root = routes.group(
        prefix="/api", middleware=[outer], access=RouteAccess.AUTHENTICATED
    )
    child = root.group(prefix="/v1", middleware=[inner])
    child.get("/x", _noop_handler).name("x")

    assert routes.specs()[0].middleware == (outer, inner)


def test_route_middleware_appends_after_group():
    routes = RouteRegistry()
    base = routes.group(
        prefix="/api/admin", name="admin.", middleware=[require_admin], access=RouteAccess.ADMIN
    )

    def _extra():
        pass

    base.get("/x", _admin_handler).middleware(_extra).name("x")
    spec = routes.specs()[0]
    assert spec.middleware == (require_admin, _extra)


def test_repeated_route_middleware_and_permission_calls_append():
    routes = RouteRegistry()

    def _extra1():
        pass

    def _extra2():
        pass

    base = routes.group(
        prefix="/api/admin",
        name="admin.",
        middleware=[require_admin],
        access=RouteAccess.ADMIN,
    )
    base.get("/x", _admin_handler).middleware(_extra1).middleware(
        _extra2
    ).permission("admin:x:a").permission("admin:x:b").name("x")

    spec = routes.specs()[0]
    assert spec.middleware == (require_admin, _extra1, _extra2)
    assert spec.permissions == ("admin:x:a", "admin:x:b")


def test_without_middleware_requires_callables():
    routes = RouteRegistry()
    base = routes.group(
        prefix="/api/admin", name="admin.", middleware=[require_admin], access=RouteAccess.ADMIN
    )
    with pytest.raises(ValueError):
        base.get("/x", _admin_handler).without_middleware()


def test_without_middleware_removes_specific_callable():
    routes = RouteRegistry()
    base = routes.group(
        prefix="/api/admin",
        name="admin.",
        middleware=[require_admin, require_auth],
        access=RouteAccess.ADMIN,
    )
    base.get("/x", _admin_handler).without_middleware(require_admin).name("x")
    spec = routes.specs()[0]
    # 只删除指定 callable，保留其余
    assert require_admin not in spec.middleware
    assert require_auth in spec.middleware


# ==================== Route ID ====================

def test_route_id_must_be_unique_on_validate():
    routes = RouteRegistry()
    g = routes.group(prefix="/api", name="a.", access=RouteAccess.AUTHENTICATED)
    g.get("/x", _noop_handler).name("dup")
    g.get("/y", _noop_handler).name("dup")
    with pytest.raises(RegistryValidationError) as ei:
        routes.validate()
    assert "duplicate route_id" in "\n".join(ei.value.errors)


def test_route_id_required():
    routes = RouteRegistry()
    g = routes.group(prefix="/api", name="a.", access=RouteAccess.AUTHENTICATED)
    g.get("/x", _noop_handler)  # 未调用 .name()
    with pytest.raises(RegistryValidationError) as ei:
        routes.validate()
    assert "missing route_id" in "\n".join(ei.value.errors)


def test_route_id_chain_order_independent():
    routes = RouteRegistry()
    g = routes.group(prefix="/api/admin", name="admin.", access=RouteAccess.ADMIN)
    a = g.get("/a", _noop_handler).permission("admin:x:list").name("a")
    b = g.get("/b", _noop_handler).name("b").permission("admin:y:list")

    ids = {s.route_id: (s.permissions, s.middleware) for s in routes.specs()}
    assert ids["admin.a"] == (("admin:x:list",), ())
    assert ids["admin.b"] == (("admin:y:list",), ())


def test_permission_deduplicates_within_and_across_calls():
    routes = RouteRegistry()
    g = routes.group(prefix="/api/admin", name="admin.", access=RouteAccess.ADMIN)
    g.get("/x", _admin_handler).permission("admin:x:list", "admin:x:list").permission(
        "admin:x:list", "admin:x:detail"
    ).name("x")

    assert routes.specs()[0].permissions == (
        "admin:x:list",
        "admin:x:detail",
    )


# ==================== 碰撞 ====================

def test_duplicate_method_path_rejected():
    routes = RouteRegistry()
    g = routes.group(prefix="/api", name="a.", access=RouteAccess.AUTHENTICATED)
    g.get("/x", _noop_handler).name("x1")
    g.get("/x", _noop_handler).name("x2")
    with pytest.raises(RegistryValidationError) as ei:
        routes.validate()
    assert "duplicate HTTP route: GET /api/x" in "\n".join(ei.value.errors)


# ==================== match / any ====================

def test_match_rejects_empty():
    routes = RouteRegistry()
    g = routes.group(prefix="/api", name="a.", access=RouteAccess.AUTHENTICATED)
    with pytest.raises(ValueError):
        g.match([], "/x", _noop_handler)


def test_match_rejects_duplicate_methods():
    routes = RouteRegistry()
    g = routes.group(prefix="/api", name="a.", access=RouteAccess.AUTHENTICATED)
    with pytest.raises(ValueError):
        g.match(["GET", "get"], "/x", _noop_handler)


def test_match_rejects_unknown_method():
    routes = RouteRegistry()
    g = routes.group(prefix="/api", name="a.", access=RouteAccess.AUTHENTICATED)
    with pytest.raises(ValueError):
        g.match(["FOO"], "/x", _noop_handler)


def test_any_under_api_requires_options_override():
    routes = RouteRegistry()
    g = routes.group(prefix="/api", name="a.", access=RouteAccess.AUTHENTICATED)
    g.any("/cb", _noop_handler).name("cb")
    with pytest.raises(RegistryValidationError) as ei:
        routes.validate()
    assert "any() under /api/* requires .allow_options_override(True)" in "\n".join(ei.value.errors)


def test_any_with_options_override_passes():
    routes = RouteRegistry()
    g = routes.group(
        prefix="/api",
        name="a.",
        middleware=[require_auth],
        access=RouteAccess.AUTHENTICATED,
    )
    g.any("/cb", _noop_handler).allow_options_override(True).name("cb")
    routes.validate()  # 不抛错


def test_any_outside_api_allowed_without_override():
    routes = RouteRegistry()
    g = routes.group(name="a.", access=RouteAccess.PUBLIC)
    g.any("/cb", _noop_handler).name("cb")
    routes.validate()


# ==================== fallback ====================

def test_fallback_rejects_non_parameterized():
    routes = RouteRegistry()
    g = routes.group(name="a.", access=RouteAccess.PUBLIC)
    with pytest.raises(ValueError):
        g.fallback("/static", _noop_handler)


def test_fallback_is_last_priority():
    routes = RouteRegistry()
    g = routes.group(name="a.", access=RouteAccess.PUBLIC)
    g.fallback("/{name}", _name_handler).name("fb")
    g.get("/health", _noop_handler).name("health")
    specs = routes._ordered_specs(include_fallback=True)
    assert [s.route_id for s in specs] == ["a.health", "a.fb"]


def test_fallback_must_be_parameterized_and_single():
    routes = RouteRegistry()
    g = routes.group(name="a.", access=RouteAccess.PUBLIC)
    g.fallback("/{a}", _param_handler).name("fb1")
    g.fallback("/{b}", _param_handler).name("fb2")
    with pytest.raises(RegistryValidationError) as ei:
        routes.validate()
    assert "only one fallback route" in "\n".join(ei.value.errors)


def test_fallback_includes_get():
    routes = RouteRegistry()
    g = routes.group(name="a.", access=RouteAccess.PUBLIC)
    g.match(["POST"], "/{x}", _param_handler).priority(RoutePriority.FALLBACK).name("fb")
    with pytest.raises(RegistryValidationError) as ei:
        routes.validate()
    assert "fallback must include GET" in "\n".join(ei.value.errors)


# ==================== handler / path 一致性 ====================

def test_handler_not_callable_rejected():
    routes = RouteRegistry()
    g = routes.group(prefix="/api", name="a.", access=RouteAccess.AUTHENTICATED)
    g.get("/x", 42).name("x")
    with pytest.raises(RegistryValidationError) as ei:
        routes.validate()
    assert "handler is not callable" in "\n".join(ei.value.errors)


def test_path_param_missing_in_handler():
    routes = RouteRegistry()
    g = routes.group(name="a.", access=RouteAccess.PUBLIC)

    async def bad_handler():
        return {}

    g.get("/file/{file_id}", bad_handler).name("f")
    with pytest.raises(RegistryValidationError) as ei:
        routes.validate()
    assert "path parameter 'file_id' missing from handler signature" in "\n".join(ei.value.errors)


def test_handler_non_path_params_not_rejected():
    """Handler 的 body/query 参数（无默认值）不触发 path 参数硬报错。

    `dto: LoginDto` 由 FastAPI 绑定为 body、`user_id: int` 绑定为 query，
    无法静态证明它们不属于 path，因此校验器不报错（设计文档 §4.3 保持
    类型化参数能力）。
    """
    routes = RouteRegistry()
    g = routes.group(name="a.", access=RouteAccess.PUBLIC)

    async def handler(n: int):
        return {}

    g.get("/static", handler).name("s")
    routes.validate()  # 不抛错


# ==================== 动态路径遮蔽 ====================

def test_dynamic_shadows_static():
    assert _dynamic_shadows_static(["/api", "{id}"], ["/api", "stats"]) is True
    assert _dynamic_shadows_static(["/api", "stats"], ["/api", "{id}"]) is False
    assert _dynamic_shadows_static(["/api", "{id}", "sub"], ["/api", "stats", "sub"]) is True
    assert _dynamic_shadows_static(["/api", "a"], ["/api", "b"]) is False


def test_dynamic_shadow_validation_fires():
    routes = RouteRegistry()
    g = routes.group(name="a.", access=RouteAccess.PUBLIC)
    g.get("/{id}", _param_handler).name("dyn")
    g.get("/stats", _noop_handler).name("stats")
    with pytest.raises(RegistryValidationError) as ei:
        routes.validate()
    assert "shadows later static path" in "\n".join(ei.value.errors)


# ==================== permission / access ====================

def test_permission_requires_admin_access():
    routes = RouteRegistry()
    g = routes.group(prefix="/api", name="a.", access=RouteAccess.AUTHENTICATED)
    g.get("/x", _noop_handler).permission("a:x").name("x")
    with pytest.raises(RegistryValidationError) as ei:
        routes.validate()
    assert "permissions but access must be ADMIN" in "\n".join(ei.value.errors)


def test_admin_access_requires_middleware():
    routes = RouteRegistry()
    g = routes.group(prefix="/api/admin", name="a.", access=RouteAccess.ADMIN)
    # 没有 require_admin，且没有 permission → 校验失败
    g.get("/x", _noop_handler).name("x")
    with pytest.raises(RegistryValidationError) as ei:
        routes.validate()
    assert "access=ADMIN" in "\n".join(ei.value.errors)


def test_admin_public_allowed_without_middleware():
    routes = RouteRegistry()
    g = routes.group(prefix="/api/admin", name="a.", access=RouteAccess.PUBLIC)
    g.get("/login", _noop_handler).name("login")
    routes.validate()


def test_admin_access_is_enforced_outside_admin_prefix():
    routes = RouteRegistry()
    g = routes.group(prefix="/api", name="private.", access=RouteAccess.ADMIN)
    g.get("/file/{x}", _param_handler).name("file")
    with pytest.raises(RegistryValidationError) as ei:
        routes.validate()
    assert "access=ADMIN" in "\n".join(ei.value.errors)


def test_auth_policy_name_spoof_does_not_satisfy_admin_access():
    routes = RouteRegistry()

    def require_admin():  # 同名但不是 app.deps.require_admin
        pass

    g = routes.group(
        prefix="/api/admin",
        name="spoof.",
        middleware=[require_admin],
        access=RouteAccess.ADMIN,
    )
    g.get("/x", _noop_handler).name("x")
    with pytest.raises(RegistryValidationError) as ei:
        routes.validate()
    assert "lacks require_admin" in "\n".join(ei.value.errors)


def test_public_access_conflicting_with_auth_middleware_rejected():
    routes = RouteRegistry()
    g = routes.group(
        prefix="/api/public",
        name="public.",
        middleware=[require_auth],
        access=RouteAccess.PUBLIC,
    )
    g.get("/x", _noop_handler).name("x")
    with pytest.raises(RegistryValidationError) as ei:
        routes.validate()
    assert "access=PUBLIC conflicts" in "\n".join(ei.value.errors)


def test_authenticated_access_requires_auth_policy():
    routes = RouteRegistry()
    g = routes.group(prefix="/api/private", name="private.", access=RouteAccess.AUTHENTICATED)
    g.get("/x", _noop_handler).name("x")
    with pytest.raises(RegistryValidationError) as ei:
        routes.validate()
    assert "access=AUTHENTICATED" in "\n".join(ei.value.errors)


def test_non_public_mount_rejected_until_mount_policy_pipeline_exists():
    routes = RouteRegistry()
    g = routes.group(name="m.", access=RouteAccess.PUBLIC)
    g.mount("/private", object(), name="private", access=RouteAccess.ADMIN)
    with pytest.raises(RegistryValidationError) as ei:
        routes.validate()
    assert "currently only supports PUBLIC" in "\n".join(ei.value.errors)


def test_admin_admin_with_permission_passes():
    routes = RouteRegistry()
    g = routes.group(prefix="/api/admin", name="a.", access=RouteAccess.ADMIN)
    g.get("/x", _admin_handler).permission("admin:x:list").name("x")
    routes.validate()


def test_client_access_boundary():
    routes = RouteRegistry()
    # access=CLIENT 但无 require_client → 失败
    g = routes.group(prefix="/api/client", name="a.", access=RouteAccess.CLIENT)
    g.get("/x", _noop_handler).name("x")
    with pytest.raises(RegistryValidationError) as ei:
        routes.validate()
    assert "lacks require_client" in "\n".join(ei.value.errors)

    # access=CLIENT + require_client → 通过
    routes2 = RouteRegistry()
    g2 = routes2.group(
        prefix="/api/client", name="a.", middleware=[require_client], access=RouteAccess.CLIENT
    )
    g2.get("/x", _noop_handler).name("x")
    routes2.validate()


# ==================== operationId / mount ====================

def test_duplicate_operation_id_rejected():
    routes = RouteRegistry()
    g = routes.group(name="a.", access=RouteAccess.PUBLIC)
    g.get("/x", _noop_handler).operation_id("op").name("x")
    g.get("/y", _noop_handler).operation_id("op").name("y")
    with pytest.raises(RegistryValidationError) as ei:
        routes.validate()
    assert "duplicate operation_id" in "\n".join(ei.value.errors)


def test_duplicate_mount_rejected():
    routes = RouteRegistry()
    g = routes.group(name="a.", access=RouteAccess.PUBLIC)
    dummy = object()
    g.mount("/uploads", dummy, name="uploads")
    g.mount("/uploads", dummy, name="uploads2")
    with pytest.raises(RegistryValidationError) as ei:
        routes.validate()
    assert "duplicate mount" in "\n".join(ei.value.errors)


# ==================== route option 白名单 ====================

def test_untyped_route_option_escape_hatch_is_not_exposed():
    routes = RouteRegistry()
    g = routes.group(name="a.", access=RouteAccess.PUBLIC)
    builder = g.get("/x", _noop_handler)
    assert not hasattr(builder, "route_option")


# ==================== catalog ====================

def test_catalog_stable_sorted_fixed_keys():
    routes = RouteRegistry()
    g = routes.group(prefix="/api/admin", name="admin.", access=RouteAccess.ADMIN)
    g.get("/z", _admin_handler).permission("admin:z:list").name("z")
    g.post("/a", _noop_handler).name("a")

    c1 = routes.catalog()
    c2 = routes.catalog()
    assert c1 == c2

    keys = ["ROUTE_ID", "METHODS", "PATH", "HANDLER", "GROUP", "ACCESS", "MIDDLEWARE",
            "PERMISSIONS", "TAGS", "RESPONSE_MODEL", "OPERATION_ID", "PRIORITY",
            "SOURCE_FILE:LINE"]
    assert list(c1[0].keys()) == keys

    paths = [e["PATH"] for e in c1]
    assert paths == sorted(paths)
    assert all(e["OPERATION_ID"] for e in c1)
    assert all(e["ACCESS"] in {"public", "authenticated", "admin", "client"} for e in c1)
    assert all("<stdin>" not in e["SOURCE_FILE:LINE"] for e in c1)


def test_catalog_json_bytes_stable():
    import json as _json

    routes = RouteRegistry()
    g = routes.group(prefix="/api/admin", name="admin.", access=RouteAccess.ADMIN)
    g.get("/z", _admin_handler).permission("admin:z:list").name("z")
    g.post("/a", _noop_handler).name("a")

    d1 = _json.dumps(routes.catalog(), ensure_ascii=False, sort_keys=False, indent=2)
    d2 = _json.dumps(routes.catalog(), ensure_ascii=False, sort_keys=False, indent=2)
    assert d1 == d2


# ==================== install 顺序 ====================

def test_install_order_http_mount_fallback():
    from fastapi import FastAPI

    app = FastAPI()
    routes = RouteRegistry()
    g = routes.group(name="a.", access=RouteAccess.PUBLIC)
    g.fallback("/{name}", _name_handler).name("fb")
    g.get("/health", _noop_handler).name("health")
    g.mount("/uploads", object(), name="uploads")

    routes.validate()
    routes.install(app)

    # FastAPI 0.141 把 include_router 包装为 _IncludedRouter：
    # - 普通 HTTP router 的路径在 original_router.routes 中；
    # - Mount 是独立实例；
    # - fallback router 是第二个 _IncludedRouter，其路径在 original_router.routes 中。
    # 验证 普通HTTP → mount → fallback 的安装顺序。
    from starlette.routing import Mount

    mounted_index = None
    normal_paths: list[str] = []
    fallback_paths: list[str] = []
    builtin = ("/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect")
    seen_router = 0
    for r in app.routes:
        p = getattr(r, "path", None)
        if isinstance(r, Mount):
            mounted_index = len(normal_paths) + 1  # mount 跟在 normal router 之后
            continue
        if p is not None:
            if p not in builtin:
                normal_paths.append(p)
            continue
        # _IncludedRouter
        inner = getattr(r, "original_router", None)
        if inner is None:
            continue
        seen_router += 1
        inner_paths = [
            getattr(x, "path", None)
            for x in getattr(inner, "routes", [])
            if getattr(x, "path", None) is not None
        ]
        if seen_router == 1:
            normal_paths.extend(inner_paths)
        elif seen_router == 2:
            fallback_paths.extend(inner_paths)

    assert normal_paths == ["/health"]
    assert fallback_paths == ["/{name}"]
    # normal HTTP router 1 个 + Mount 1 个：mount 紧跟 normal router 之后（index=2），
    # fallback router 在其后。这验证 普通HTTP → mount → fallback 的安装顺序。
    assert mounted_index == 2


# ==================== current_auth 语义 ====================

def test_current_auth_from_middleware():
    """current_auth 只从已执行的 Route middleware 获取 AuthInfo。"""
    from app.deps import current_auth

    class FakeRequest:
        def __init__(self):
            self.state = type("State", (), {"auth": object()})()

    req = FakeRequest()
    assert current_auth(req) is req.state.auth


def test_current_auth_missing_middleware_raises():
    from app.deps import current_auth

    class FakeRequest:
        def __init__(self):
            self.state = type("State", (), {})()

    req = FakeRequest()
    with pytest.raises(RuntimeError):
        current_auth(req)
