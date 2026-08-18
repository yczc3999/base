"""
集中式路由注册表 — 编译阶段强制校验

一次收集所有错误并以非零状态失败。覆盖：

1. (HTTP Method, normalized path) 重复
2. Route ID 重复或缺失
3. operation_id 重复
4. Handler 不可调用
5. path 中声明的 parameter 在 Handler 签名中缺失
6. /api/admin/* access 校验
7. /api/client/* access 校验
8. .permission() 的 RouteSpec 必须 access=ADMIN
9. Fallback 校验
10. 动态路径遮蔽
11. any() 在 /api/* 下未显式允许 OPTIONS override
12. 未知内部 RouteSpec 标志
13. Mount 名称或 path 重复
"""

from __future__ import annotations

import inspect
import re
from typing import Any, Callable

from app.routes.types import (
    RouteAccess,
    RoutePriority,
    RouteSpec,
)

def _middleware_chain_has(
    middleware: tuple[Callable[..., Any], ...], *targets: Callable[..., Any]
) -> bool:
    """只接受精确 callable identity，不用函数名猜测安全策略。

    任意业务函数都可以命名为 `require_admin`；名称匹配会让
    Catalog 校验通过而实际没有鉴权。
    """
    target_ids = {id(target) for target in targets}
    return any(id(policy) in target_ids for policy in middleware)


_PARAM_RE = re.compile(r"\{([^{}:]+)(?::[^{}]*)?\}")


def _path_params(path: str) -> set[str]:
    return set(_PARAM_RE.findall(path))


def _handler_params(handler: Callable[..., Any]) -> set[str]:
    if not callable(handler):
        return set()
    try:
        sig = inspect.signature(handler)
    except (TypeError, ValueError):
        return set()
    return {
        name
        for name, p in sig.parameters.items()
        if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    }


class RegistryValidationError(ValueError):
    """一次性收集所有校验错误。"""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("\n".join(errors))


def _error_ctx(spec: RouteSpec, message: str) -> str:
    loc = f"{spec.source_file}:{spec.source_line}" if spec.source_file else "unknown"
    return f"[{spec.route_id or '<unnamed>'} @ {loc}] {message}"


def validate_registry(registry: Any) -> None:
    errors: list[str] = []
    specs = list(registry.specs())
    mounts = list(registry.mounts())

    # ---- 1. (HTTP Method, path) 重复 ----
    seen_pairs: dict[tuple[str, str], RouteSpec] = {}
    for spec in specs:
        for method in spec.methods:
            key = (method, spec.path)
            other = seen_pairs.get(key)
            if other is not None:
                errors.append(
                    _error_ctx(
                        spec,
                        f"duplicate HTTP route: {method} {spec.path} already registered "
                        f"by {other.route_id!r}",
                    )
                )
            else:
                seen_pairs[key] = spec

    # ---- 2. Route ID 重复或缺失 ----
    seen_ids: dict[str, RouteSpec] = {}
    for spec in specs:
        if not spec.route_id:
            errors.append(_error_ctx(spec, "missing route_id (use .name(...))"))
            continue
        other = seen_ids.get(spec.route_id)
        if other is not None:
            errors.append(
                _error_ctx(
                    spec,
                    f"duplicate route_id {spec.route_id!r} already used by "
                    f"{other.source_file}:{other.source_line}",
                )
            )
        else:
            seen_ids[spec.route_id] = spec

    # ---- 3. operation_id 重复 ----
    seen_op_ids: dict[str, RouteSpec] = {}
    for spec in specs:
        if not spec.operation_id:
            continue
        other = seen_op_ids.get(spec.operation_id)
        if other is not None:
            errors.append(
                _error_ctx(
                    spec,
                    f"duplicate operation_id {spec.operation_id!r} already used by "
                    f"{other.source_file}:{other.source_line}",
                )
            )
        else:
            seen_op_ids[spec.operation_id] = spec

    # ---- 4. Handler 不可调用 ----
    for spec in specs:
        if not callable(spec.endpoint):
            errors.append(_error_ctx(spec, "handler is not callable"))

    # ---- 5. path parameter 必须与 Handler 签名一致 ----
    # 只检查「path 中声明的参数在 Handler 签名里缺失」（静态可确定）。
    # 「Handler 有无默认值的参数不在 path」无法静态区分 body/query/header
    # 绑定（如 dto: LoginDto 是 body、user_id: int 是 query），因此不做硬报错。
    for spec in specs:
        if not callable(spec.endpoint):
            continue  # 已在第 4 条报告
        path_ps = _path_params(spec.path)
        handler_ps = _handler_params(spec.endpoint)
        for p in sorted(path_ps - handler_ps):
            errors.append(_error_ctx(spec, f"path parameter {p!r} missing from handler signature"))

    # ---- 6. /api/admin/* 访问边界 ----
    from app.deps import require_admin, require_auth, require_client

    # 先按显式 RouteAccess 校验全局安全边界，不依赖 prefix。
    # 例如 `/api/file/{id}` 是 ADMIN，但它不在 `/api/admin/*`。
    for spec in specs:
        has_auth = _middleware_chain_has(
            spec.middleware, require_auth, require_admin, require_client
        )
        if spec.access is RouteAccess.ADMIN:
            if not _middleware_chain_has(spec.middleware, require_admin) and not spec.permissions:
                errors.append(
                    _error_ctx(spec, "access=ADMIN but effective middleware lacks require_admin/permission")
                )
        elif spec.access is RouteAccess.CLIENT:
            if not _middleware_chain_has(spec.middleware, require_client):
                errors.append(
                    _error_ctx(spec, "access=CLIENT but effective middleware lacks require_client")
                )
        elif spec.access is RouteAccess.AUTHENTICATED:
            if not has_auth and not spec.permissions:
                errors.append(
                    _error_ctx(spec, "access=AUTHENTICATED but effective middleware lacks auth policy")
                )
        elif spec.access is RouteAccess.PUBLIC and has_auth:
            errors.append(
                _error_ctx(spec, "access=PUBLIC conflicts with an authentication middleware")
            )

    for spec in specs:
        if spec.path.startswith("/api/admin/") or spec.path == "/api/admin":
            if spec.access not in (RouteAccess.PUBLIC, RouteAccess.ADMIN):
                errors.append(
                    _error_ctx(
                        spec,
                        f"admin route must declare access=PUBLIC|ADMIN, got {spec.access.value}",
                    )
                )

    # ---- 7. /api/client/* 访问边界 ----
    for spec in specs:
        if spec.path.startswith("/api/client/") or spec.path == "/api/client":
            if spec.access not in (RouteAccess.PUBLIC, RouteAccess.CLIENT):
                errors.append(
                    _error_ctx(
                        spec,
                        f"client route must declare access=PUBLIC|CLIENT, got {spec.access.value}",
                    )
                )

    # ---- 8. .permission() 必须 access=ADMIN ----
    for spec in specs:
        if spec.permissions and spec.access is not RouteAccess.ADMIN:
            errors.append(
                _error_ctx(
                    spec,
                    f"route declares permissions but access must be ADMIN, got {spec.access.value}",
                )
            )

    # ---- 9. Fallback 校验 ----
    fallbacks = [s for s in specs if s.priority is RoutePriority.FALLBACK]
    if fallbacks:
        # 必须包含 path 参数
        for spec in fallbacks:
            if not _path_params(spec.path):
                errors.append(_error_ctx(spec, "fallback path must be parameterized"))
        # 最多一个 fallback
        if len(fallbacks) > 1:
            for spec in fallbacks[1:]:
                errors.append(_error_ctx(spec, "only one fallback route is allowed"))
        # fallback 必须是 GET（或其 method 不包含会遮蔽其它 method 的选项）
        for spec in fallbacks:
            if "GET" not in spec.methods:
                errors.append(_error_ctx(spec, "fallback must include GET method"))

    # ---- 10. 动态路径遮蔽 ----
    for i, spec in enumerate(specs):
        if spec.priority is not RoutePriority.NORMAL:
            continue
        segs = spec.path.split("/")
        for j, later in enumerate(specs):
            if j <= i or later.priority is not RoutePriority.NORMAL:
                continue
            # 同 Method 下，动态 segment 早于静态 segment 会遮蔽
            if set(later.methods) & set(spec.methods):
                lsegs = later.path.split("/")
                if _dynamic_shadows_static(segs, lsegs):
                    errors.append(
                        _error_ctx(
                            spec,
                            f"dynamic path {spec.path!r} shadows later static path {later.path!r} "
                            f"for methods {sorted(set(spec.methods))}",
                        )
                    )

    # ---- 11. any() 在 /api/* 下未显式允许 OPTIONS override ----
    for spec in specs:
        if spec.priority is not RoutePriority.NORMAL:
            continue
        opts = spec.route_options or {}
        from_any = opts.get("from_any", False)
        allow_override = opts.get("allow_options_override", False)
        if from_any and (spec.path.startswith("/api/") or spec.path == "/api"):
            if not allow_override:
                errors.append(
                    _error_ctx(
                        spec,
                        "any() under /api/* requires .allow_options_override(True)",
                    )
                )

    # ---- 12. RouteSpec 内部标志白名单 ----
    # 不提供公开 `route_option()` 透传袋，防止绕过已校验的
    # methods/dependencies/permissions。route_options 只保存 any() 的内部元数据。
    internal_route_options = {"from_any", "allow_options_override"}
    for spec in specs:
        for key in spec.route_options:
            if key not in internal_route_options:
                errors.append(_error_ctx(spec, f"unknown internal route option {key!r}"))

    # ---- 13. Mount 名称或 path 重复 ----
    seen_mount_paths: dict[str, Any] = {}
    seen_mount_names: dict[str, Any] = {}
    for mount in mounts:
        if mount.access is not RouteAccess.PUBLIC:
            errors.append(
                f"mount {mount.name!r} declares access={mount.access.value}; "
                "MountSpec has no route dependency pipeline and currently only supports PUBLIC"
            )
        if mount.path in seen_mount_paths:
            errors.append(
                f"duplicate mount path {mount.path!r} already used by "
                f"{seen_mount_paths[mount.path].source_file}:{seen_mount_paths[mount.path].source_line}"
            )
        else:
            seen_mount_paths[mount.path] = mount
        if mount.name in seen_mount_names:
            errors.append(
                f"duplicate mount name {mount.name!r} already used by "
                f"{seen_mount_names[mount.name].source_file}:{seen_mount_names[mount.name].source_line}"
            )
        else:
            seen_mount_names[mount.name] = mount

    # ---- 汇总 ----
    if errors:
        raise RegistryValidationError(errors)


_WHOLE_SEGMENT_PARAM_RE = re.compile(r"^\{(?:[^{}:]+)(?::[^{}]*)?\}$")


def _is_whole_param_segment(segment: str) -> bool:
    """segment 是否完全等于 {param}（含 path converter）。

    只有完整动态段会遮蔽同层字面量；`sitemap-{n}.xml` 是半参数字面量，
    只匹配自身字面形式，不会遮蔽独立字面量 `/robots.txt`。
    """
    return bool(_WHOLE_SEGMENT_PARAM_RE.match(segment))


def _dynamic_shadows_static(dynamic_segs: list[str], static_segs: list[str]) -> bool:
    """判断 dynamic path 是否遮蔽 static path（同 Method）。

    规则：仅当某位置 dynamic 段是**完整** {param} 段而 static 同层是字面量段，
    且前序段完全一致时才视为遮蔽。
    """
    if dynamic_segs == static_segs:
        return False
    if len(dynamic_segs) > len(static_segs):
        return False
    for i in range(len(dynamic_segs)):
        if dynamic_segs[i] == static_segs[i]:
            continue
        # dynamic 是完整参数段、static 是同层字面量 → 遮蔽
        if _is_whole_param_segment(dynamic_segs[i]) and "{" not in static_segs[i]:
            return True
        # 其它差异（如不同字面量 /api/a vs /api/b、或半参数段）不遮蔽
        return False
    # dynamic 是 static 的前缀（如 /api/{x} vs /api/stats/sub）
    if len(dynamic_segs) < len(static_segs):
        if dynamic_segs and _is_whole_param_segment(dynamic_segs[-1]):
            return True
    return False
