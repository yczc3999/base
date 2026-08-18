"""
集中式路由注册表 — Registry / Group / Builder

Laravel 风格 DSL：group / middleware / get / post / put / patch / delete /
options / head / match / any / fallback / mount / crud。

- Group 属性通过创建新不可变 GroupSpec 继承。
- RouteBuilder 的链式方法在每次调用后提交（replace），保证
  `.permission(...).name(...)` 与 `.name(...).permission(...)` 顺序无关。
- 编译/安装顺序固定为：普通 HTTP route → mount → fallback。
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from fastapi import APIRouter, Depends

from app.deps import require_perms
from app.routes.types import (
    ANY_METHODS,
    HTTP_METHODS,
    GroupSpec,
    MountSpec,
    RouteAccess,
    RoutePriority,
    RouteSpec,
    join_prefix,
    normalize_methods,
)

_KnownMiddleware = Callable[..., Any]

_INTERNAL_ROUTE_FILES = frozenset({"registry.py", "resources.py", "legacy.py"})
_SERVE_ROOT = Path(__file__).resolve().parents[2]


def _stable_source_file(filename: str) -> str:
    """将仓库内来源规范化为 serve-relative 路径，保证跨工作树可 diff。"""
    try:
        return Path(filename).resolve().relative_to(_SERVE_ROOT).as_posix()
    except (OSError, ValueError):
        return filename


def _caller_frame() -> tuple[str, int]:
    """找到真正的 Manifest 声明帧，作为 source_file/line。

    只跳过 Registry/CRUD 内部工厂，不能跳过
    `app/routes/admin.py` / `system.py` 等权威 Manifest。
    """
    frame = inspect.currentframe()
    try:
        f = frame
        while f is not None:
            filename = f.f_code.co_filename
            path = Path(filename)
            is_internal = path.parent.name == "routes" and path.name in _INTERNAL_ROUTE_FILES
            if not is_internal:
                return _stable_source_file(filename), f.f_lineno
            f = f.f_back
        return "", 0
    finally:
        del frame


class RouteBuilder:
    """单条路由的链式元数据配置器。

    - `.name(route_id)` 必须被调用，否则 route_id 为空且在 validate 阶段失败。
    - 每次链式方法调用后提交当前状态；重复提交会替换旧 spec。
    """

    def __init__(
        self,
        registry: "RouteRegistry",
        methods: Iterable[str],
        path: str,
        endpoint: Callable[..., Any],
        group: GroupSpec,
        *,
        source_file: str = "",
        source_line: int = 0,
        priority: RoutePriority = RoutePriority.NORMAL,
        from_any: bool = False,
    ) -> None:
        self._registry = registry
        self._methods = normalize_methods(tuple(methods))
        self._path = path
        self._endpoint = endpoint
        self._group = group
        self._access = group.access
        caller_file, caller_line = _caller_frame()
        self._source_file = source_file or caller_file
        self._source_line = source_line or caller_line

        self._route_id = ""
        self._permissions: tuple[str, ...] = ()
        self._middleware_extra: tuple[_KnownMiddleware, ...] = ()
        self._without_middleware: tuple[_KnownMiddleware, ...] = ()
        self._tags: tuple[str, ...] | None = None
        self._response_model: Any = None
        self._responses: Mapping[int | str, Mapping[str, Any]] | None = None
        self._status_code: int | None = None
        self._summary: str | None = None
        self._description: str | None = None
        self._deprecated: bool | None = None
        self._include_in_schema: bool | None = None
        self._operation_id: str | None = None
        self._priority = priority
        self._from_any = from_any
        self._allow_options_override = False
        self._route_options: dict[str, Any] = {}
        self._committed = False
        self._spec: RouteSpec | None = None

        # 初始提交：route_id 为空，validate 阶段会拒绝未命名路由
        self._commit()

    # ---- 元数据链式方法（每次调用后提交） ----

    def name(self, route_id: str) -> "RouteBuilder":
        if not route_id or not isinstance(route_id, str):
            raise ValueError("route_id must be a non-empty string")
        self._route_id = route_id
        self._commit()
        return self

    def middleware(self, *policies: _KnownMiddleware) -> "RouteBuilder":
        self._middleware_extra += tuple(policies)
        self._commit()
        return self

    def without_middleware(self, *policies: _KnownMiddleware) -> "RouteBuilder":
        if not policies:
            raise ValueError(
                "without_middleware() requires at least one callable; use a Public group instead"
            )
        self._without_middleware += tuple(policies)
        self._commit()
        return self

    def permission(self, *permissions: str) -> "RouteBuilder":
        if not permissions:
            raise ValueError("permission() requires at least one permission string")
        for p in permissions:
            if not isinstance(p, str) or not p:
                raise ValueError("permission strings must be non-empty")
            if p not in self._permissions:
                self._permissions += (p,)
        self._commit()
        return self

    def tags(self, *tags: str) -> "RouteBuilder":
        self._tags = tuple(tags)
        self._commit()
        return self

    def response_model(self, model: Any) -> "RouteBuilder":
        self._response_model = model
        self._commit()
        return self

    def responses(self, mapping: Mapping[int | str, Mapping[str, Any]]) -> "RouteBuilder":
        self._responses = dict(mapping)
        self._commit()
        return self

    def status_code(self, code: int) -> "RouteBuilder":
        self._status_code = code
        self._commit()
        return self

    def summary(self, text: str) -> "RouteBuilder":
        self._summary = text
        self._commit()
        return self

    def description(self, text: str) -> "RouteBuilder":
        self._description = text
        self._commit()
        return self

    def deprecated(self, flag: bool = True) -> "RouteBuilder":
        self._deprecated = bool(flag)
        self._commit()
        return self

    def include_in_schema(self, flag: bool = True) -> "RouteBuilder":
        self._include_in_schema = bool(flag)
        self._commit()
        return self

    def operation_id(self, value: str) -> "RouteBuilder":
        self._operation_id = value
        self._commit()
        return self

    def priority(self, value: RoutePriority) -> "RouteBuilder":
        if not isinstance(value, RoutePriority):
            raise TypeError("priority must be a RoutePriority")
        self._priority = value
        self._commit()
        return self

    def access(self, value: RouteAccess) -> "RouteBuilder":
        """显式覆盖单条路由的访问边界。

        主要用于一个资源同时保留 protected/public action 的兼容场景；
        值仍由编译期安全校验约束，不能作为鉴权豁免开关。
        """
        if not isinstance(value, RouteAccess):
            raise TypeError("access must be a RouteAccess")
        self._access = value
        self._commit()
        return self

    def allow_options_override(self, flag: bool = True) -> "RouteBuilder":
        """允许 any() 路由出现在 /api/* 下（覆盖 CORS preflight 保护）。"""
        self._allow_options_override = bool(flag)
        self._commit()
        return self

    # ---- 内部 ----

    def _build_spec(self) -> RouteSpec:
        full_path = join_prefix(self._group.prefix, self._path)

        middleware_list: list[_KnownMiddleware] = list(self._group.middleware)
        remove_ids = {id(p) for p in self._without_middleware}
        middleware_list = [m for m in middleware_list if id(m) not in remove_ids]
        middleware_list.extend(self._middleware_extra)

        # 精确相同 callable 去重，保留首次出现顺序
        seen: set[int] = set()
        deduped: list[_KnownMiddleware] = []
        for m in middleware_list:
            if id(m) not in seen:
                seen.add(id(m))
                deduped.append(m)
        middleware = tuple(deduped)

        route_id = f"{self._group.name}{self._route_id}" if self._route_id else ""

        route_options = dict(self._route_options)
        if self._from_any:
            route_options["from_any"] = True
            route_options["allow_options_override"] = self._allow_options_override

        return RouteSpec(
            methods=self._methods,
            path=full_path,
            endpoint=self._endpoint,
            route_id=route_id,
            access=self._access,
            middleware=middleware,
            permissions=self._permissions,
            tags=self._tags if self._tags is not None else self._group.tags,
            response_model=self._response_model,
            responses=self._responses if self._responses is not None else self._group.responses,
            status_code=self._status_code,
            operation_id=self._operation_id,
            include_in_schema=self._include_in_schema if self._include_in_schema is not None else True,
            deprecated=self._deprecated if self._deprecated is not None else self._group.deprecated,
            priority=self._priority,
            source_file=self._source_file,
            source_line=self._source_line,
            group_path=self._group.name.rstrip("."),
            route_options=route_options,
        )

    def _commit(self) -> None:
        spec = self._build_spec()
        if self._committed:
            self._registry._replace_spec(self._spec, spec)  # noqa: SLF001
        else:
            self._registry._register_spec(spec)  # noqa: SLF001
        self._spec = spec
        self._committed = True

    @property
    def route_id(self) -> str:
        return self._spec.route_id


class RouteGroup:
    """一组路由的声明容器。

    Group 属性通过 with_overrides 继承，返回新 RouteGroup。
    """

    def __init__(self, registry: "RouteRegistry", spec: GroupSpec) -> None:
        self._registry = registry
        self._spec = spec

    @property
    def spec(self) -> GroupSpec:
        return self._spec

    # ---- Group 嵌套 ----

    def group(
        self,
        prefix: str | None = None,
        name: str | None = None,
        tags: Iterable[str] | None = None,
        middleware: Iterable[_KnownMiddleware] | None = None,
        access: RouteAccess | None = None,
        responses: Mapping[int | str, Mapping[str, Any]] | None = None,
        deprecated: bool | None = None,
    ) -> "RouteGroup":
        overrides: dict[str, Any] = {}
        if prefix is not None:
            overrides["prefix"] = prefix
        if name is not None:
            overrides["name"] = name
        if tags is not None:
            overrides["tags"] = tuple(tags)
        if middleware is not None:
            overrides["middleware"] = tuple(middleware)
        if access is not None:
            overrides["access"] = access
        if responses is not None:
            overrides["responses"] = dict(responses)
        if deprecated is not None:
            overrides["deprecated"] = deprecated
        new_spec = self._spec.with_overrides(**overrides)
        return RouteGroup(self._registry, new_spec)

    # ---- HTTP verb ----

    def get(self, path: str, handler: Callable[..., Any]) -> RouteBuilder:
        return self._add_route(("GET",), path, handler)

    def post(self, path: str, handler: Callable[..., Any]) -> RouteBuilder:
        return self._add_route(("POST",), path, handler)

    def put(self, path: str, handler: Callable[..., Any]) -> RouteBuilder:
        return self._add_route(("PUT",), path, handler)

    def patch(self, path: str, handler: Callable[..., Any]) -> RouteBuilder:
        return self._add_route(("PATCH",), path, handler)

    def delete(self, path: str, handler: Callable[..., Any]) -> RouteBuilder:
        return self._add_route(("DELETE",), path, handler)

    def options(self, path: str, handler: Callable[..., Any]) -> RouteBuilder:
        return self._add_route(("OPTIONS",), path, handler)

    def head(self, path: str, handler: Callable[..., Any]) -> RouteBuilder:
        return self._add_route(("HEAD",), path, handler)

    def match(
        self, methods: Sequence[str], path: str, handler: Callable[..., Any]
    ) -> RouteBuilder:
        if not methods:
            raise ValueError("match() requires at least one HTTP method")
        raw = tuple(m.upper() for m in methods)
        if len(raw) != len(set(raw)):
            raise ValueError("duplicate HTTP methods in match()")
        norm = normalize_methods(raw)
        unknown = [m for m in norm if m not in HTTP_METHODS]
        if unknown:
            raise ValueError(f"unknown HTTP methods: {unknown}")
        return self._add_route(norm, path, handler)

    def any(self, path: str, handler: Callable[..., Any]) -> RouteBuilder:
        """GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS。

        在 /api/* 下默认校验失败（可能抢占 CORS preflight），
        只有 `.allow_options_override(True)` 后才允许。
        """
        return self._add_route(ANY_METHODS, path, handler, from_any=True)

    def fallback(self, path: str, handler: Callable[..., Any]) -> RouteBuilder:
        """根兜底路由。

        只允许参数化或 path-converter 路径，自动标记 RoutePriority.FALLBACK，
        并在所有普通 HTTP 路由与 Mount 之后编译。
        """
        if "{" not in path:
            raise ValueError("fallback() only accepts parameterized or path-converter paths")
        return self._add_route(("GET",), path, handler, priority=RoutePriority.FALLBACK)

    def mount(
        self, path: str, app: Any, name: str, access: RouteAccess = RouteAccess.PUBLIC
    ) -> None:
        """声明静态挂载（如 /uploads）。"""
        if not name:
            raise ValueError("mount name must be non-empty")
        source_file, source_line = _caller_frame()
        self._registry._mounts.append(  # noqa: SLF001
            MountSpec(
                path=join_prefix(path),
                app=app,
                name=name,
                access=access,
                source_file=source_file,
                source_line=source_line,
                group_path=self._spec.name.rstrip("."),
            )
        )

    # ---- CRUD ----

    def crud(
        self,
        prefix: str,
        logic: Any,
        *,
        name: str | None = None,
        tags: list[str] | None = None,
        permissions: str = "",
        need_auth: bool = True,
        no_auth: list[str] | None = None,
        auth_dep: Callable[..., Any] | None = None,
        perms_prefix: str = "",
        actions: dict[str, list[str]] | None = None,
        only: list[str] | None = None,
        except_: list[str] | None = None,
    ) -> None:
        """以一条资源声明注册标准 CRUD。

        - name 为可选；缺省时使用 prefix 作为 CRUD 资源名。
        - 生成的 5 个契约端点进入全局目录。
        """
        from app.routes import resources

        resources.register_legacy_crud(
            self,
            prefix=prefix,
            logic=logic,
            name=name or prefix.strip("/"),
            tags=tags,
            need_auth=need_auth,
            no_auth=no_auth,
            auth_dep=auth_dep,
            perms_prefix=perms_prefix or permissions,
            actions=actions,
            only=only,
            except_=except_,
        )

    # ---- 内部 ----

    def _add_route(
        self,
        methods: Iterable[str],
        path: str,
        handler: Callable[..., Any],
        *,
        priority: RoutePriority = RoutePriority.NORMAL,
        from_any: bool = False,
    ) -> RouteBuilder:
        # Handler 不可调用在 validate 阶段收集（设计文档第 4/10 节），
        # 注册阶段不提前失败，保证 validate() 能一次报告全部错误。
        return RouteBuilder(
            self._registry,
            methods,
            path,
            handler,
            self._spec,
            priority=priority,
            from_any=from_any,
        )


class RouteRegistry:
    """全局路由注册表。

    - `group()` 创建根 Group。
    - `validate()` 执行编译阶段校验。
    - `install(app)` 按 普通 HTTP → mount → fallback 顺序安装。
    - `catalog()` 输出确定性路由目录。
    """

    def __init__(self) -> None:
        self._specs: list[RouteSpec] = []
        self._mounts: list[MountSpec] = []

    # ---- 注册 ----

    def group(
        self,
        prefix: str = "",
        name: str = "",
        tags: Iterable[str] | None = None,
        middleware: Iterable[_KnownMiddleware] | None = None,
        access: RouteAccess = RouteAccess.AUTHENTICATED,
        responses: Mapping[int | str, Mapping[str, Any]] | None = None,
        deprecated: bool = False,
    ) -> RouteGroup:
        spec = GroupSpec(
            prefix=join_prefix(prefix) if prefix else "",
            name=name,
            tags=tuple(tags or ()),
            middleware=tuple(middleware or ()),
            access=access,
            responses=dict(responses or {}),
            deprecated=deprecated,
        )
        return RouteGroup(self, spec)

    def _register_spec(self, spec: RouteSpec) -> RouteSpec:
        self._specs.append(spec)
        return spec

    def _replace_spec(self, old: RouteSpec, new: RouteSpec) -> None:
        for i, s in enumerate(self._specs):
            if s is old:
                self._specs[i] = new
                return
        raise RuntimeError("cannot replace unregistered spec")

    # ---- 编译 ----

    def _ordered_specs(
        self, *, include_fallback: bool = False, only_fallback: bool = False
    ) -> list[RouteSpec]:
        normal = [s for s in self._specs if s.priority is RoutePriority.NORMAL]
        fallback = [s for s in self._specs if s.priority is RoutePriority.FALLBACK]
        if only_fallback:
            return fallback
        if include_fallback:
            return normal + fallback
        return normal

    def compile_http_router(
        self, *, include_fallback: bool = False, only_fallback: bool = False
    ) -> APIRouter:
        """把 HTTP RouteSpec 显式映射为 FastAPI APIRouter。

        禁止用一个无类型 `**kwargs` 袋透传未校验参数。
        """
        router = APIRouter()
        for spec in self._ordered_specs(
            include_fallback=include_fallback, only_fallback=only_fallback
        ):
            deps = [Depends(m) for m in spec.middleware]
            if spec.permissions:
                deps.append(Depends(require_perms(*spec.permissions)))

            kwargs: dict[str, Any] = {
                "methods": list(spec.methods),
                "path": spec.path,
                "endpoint": spec.endpoint,
                "dependencies": deps,
                "tags": list(spec.tags),
                "include_in_schema": spec.include_in_schema,
                "deprecated": spec.deprecated,
            }
            if spec.response_model is not None:
                kwargs["response_model"] = spec.response_model
            if spec.responses:
                kwargs["responses"] = dict(spec.responses)
            if spec.status_code is not None:
                kwargs["status_code"] = spec.status_code
            if spec.operation_id:
                kwargs["operation_id"] = spec.operation_id

            router.add_api_route(**kwargs)
        return router

    # ---- 校验与安装 ----

    def validate(self) -> None:
        from app.routes.validation import validate_registry

        validate_registry(self)

    def install(self, app: Any) -> None:
        """按 普通 HTTP → mount → fallback 顺序安装。

        普通 HTTP 路由与 fallback 分别编译，避免重复注册：
        同一 (method, path) 在 FastAPI 中后注册者遮蔽先注册者，
        重复编译 normal 会把普通路由再次插在 fallback 之前。
        """
        normal_router = self.compile_http_router(include_fallback=False)
        app.include_router(normal_router)

        for mount in self._mounts:
            app.mount(mount.path, mount.app, name=mount.name)

        fallback_router = self.compile_http_router(only_fallback=True)
        if fallback_router.routes:
            app.include_router(fallback_router)

    # ---- 目录 ----

    def specs(self) -> tuple[RouteSpec, ...]:
        return tuple(self._specs)

    def mounts(self) -> tuple[MountSpec, ...]:
        return tuple(self._mounts)

    def catalog(self) -> list[dict[str, Any]]:
        from app.routes.catalog import build_catalog

        return build_catalog(self)
