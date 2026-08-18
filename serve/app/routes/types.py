"""
集中式路由注册表 — 数据契约

定义 RouteSpec / GroupSpec / RoutePriority / RouteAccess 等不可变数据结构。
本文件只包含数据契约，不包含编译或校验逻辑（见 registry.py / validation.py）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping


class RoutePriority(Enum):
    """路由优先级。

    - NORMAL：普通 HTTP 路由，按 Manifest 声明顺序编译。
    - FALLBACK：根兜底路由（如 /{name}），必须在所有普通 HTTP 路由和 Mount 之后编译。
    """

    NORMAL = "normal"
    FALLBACK = "fallback"


class RouteAccess(Enum):
    """路由访问边界。

    每个 Group / Route 必须显式声明 access，校验器不通过
    Group 名称或 prefix 猜测安全边界。
    """

    PUBLIC = "public"
    AUTHENTICATED = "authenticated"
    ADMIN = "admin"
    CLIENT = "client"


HTTP_METHODS: tuple[str, ...] = (
    "GET",
    "HEAD",
    "POST",
    "PUT",
    "PATCH",
    "DELETE",
    "OPTIONS",
)

ANY_METHODS: tuple[str, ...] = (
    "GET",
    "HEAD",
    "POST",
    "PUT",
    "PATCH",
    "DELETE",
    "OPTIONS",
)


def normalize_methods(methods: tuple[str, ...]) -> tuple[str, ...]:
    """规范化 HTTP Method 集合：大写、去重、保序。"""
    seen: set[str] = set()
    result: list[str] = []
    for m in methods:
        u = m.upper()
        if u not in seen:
            seen.add(u)
            result.append(u)
    return tuple(result)


@dataclass(frozen=True, slots=True)
class GroupSpec:
    """Route Group 的不可变属性集合。

    Group 属性通过创建新 GroupSpec 继承，不使用全局 context stack。
    """

    prefix: str = ""
    name: str = ""
    tags: tuple[str, ...] = ()
    middleware: tuple[Callable[..., Any], ...] = ()
    access: RouteAccess = RouteAccess.AUTHENTICATED
    responses: Mapping[int | str, Mapping[str, Any]] = field(default_factory=dict)
    deprecated: bool = False

    def key(self) -> tuple:
        """作为 dict key 使用。"""
        return (
            self.prefix,
            self.name,
            self.tags,
            tuple(id(m) for m in self.middleware),
            self.access,
        )

    def with_prefix(self, prefix: str) -> "GroupSpec":
        return GroupSpec(
            prefix=join_prefix(self.prefix, prefix),
            name=self.name,
            tags=self.tags,
            middleware=self.middleware,
            access=self.access,
            responses=self.responses,
            deprecated=self.deprecated,
        )

    def with_overrides(self, **kwargs: Any) -> "GroupSpec":
        """返回继承当前属性的新 GroupSpec。

        只接受 GroupSpec 的已知字段。
        """
        allowed = {f.name for f in GroupSpec.__dataclass_fields__.values()}
        unknown = set(kwargs) - allowed
        if unknown:
            raise TypeError(f"unknown GroupSpec fields: {sorted(unknown)}")
        data = {
            "prefix": self.prefix,
            "name": self.name,
            "tags": self.tags,
            "middleware": self.middleware,
            "access": self.access,
            "responses": self.responses,
            "deprecated": self.deprecated,
        }
        data.update(kwargs)
        if "prefix" in kwargs:
            data["prefix"] = join_prefix(self.prefix, kwargs["prefix"])
        if "name" in kwargs:
            # name 沿 Group 链拼接（外层 → 内层），如 "admin." + "user." = "admin.user."
            parts = [p for p in (self.name, kwargs["name"]) if p]
            joined = ".".join(p.strip(".") for p in parts)
            data["name"] = f"{joined}." if joined else ""
        if "middleware" in kwargs:
            # Laravel 风格 Group middleware 必须继承而不是覆盖：
            # 外层 → 内层，精确相同 callable 在 RouteBuilder 提交时去重。
            data["middleware"] = self.middleware + tuple(kwargs["middleware"])
        return GroupSpec(**data)


@dataclass(frozen=True, slots=True)
class RouteSpec:
    """单条 HTTP 路由的完整数据合同。

    所有字段在注册时固化，禁止用一个无类型 `**kwargs` 袋透传未校验参数。
    """

    methods: tuple[str, ...]
    path: str
    endpoint: Callable[..., Any]
    route_id: str
    access: RouteAccess
    middleware: tuple[Callable[..., Any], ...] = ()
    permissions: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    response_model: Any = None
    responses: Mapping[int | str, Mapping[str, Any]] = field(default_factory=dict)
    status_code: int | None = None
    operation_id: str | None = None
    include_in_schema: bool = True
    deprecated: bool = False
    priority: RoutePriority = RoutePriority.NORMAL
    source_file: str = ""
    source_line: int = 0
    group_path: str = ""
    route_options: Mapping[str, Any] = field(default_factory=dict)

    @property
    def path_param_names(self) -> tuple[str, ...]:
        """提取 path 中的 FastAPI 路径参数名（如 /file/{file_id} → ("file_id",)）。"""
        import re

        return tuple(re.findall(r"\{([^{}:]+)(?::[^{}]*)?\}", self.path))

    @property
    def has_path_params(self) -> bool:
        return bool(self.path_param_names)


@dataclass(frozen=True, slots=True)
class MountSpec:
    """静态挂载（如 /uploads → StaticFiles）。

    与 HTTP RouteSpec 分开表示，但都必须从 RouteRegistry 进入 App。
    """

    path: str
    app: Any
    name: str
    access: RouteAccess = RouteAccess.PUBLIC
    source_file: str = ""
    source_line: int = 0
    group_path: str = ""


def join_prefix(*parts: str) -> str:
    """规范化 prefix / path 拼接。

    - 结果必须以 / 开头。
    - 非根路径不以 / 结尾。
    - 拒绝 //、. 和 .. path segment。
    """
    if not parts:
        return "/"
    for part in parts:
        # 分片边界两侧的 `/` 会被规范化；单个分片内部的 `//`
        # 属于含混/错误路径，必须拒绝而不是静默改写。
        if part not in ("", "/") and "//" in part:
            raise ValueError(f"double slash is not allowed in path part {part!r}")
    joined = "/".join(p.strip("/") for p in parts if p)
    if not joined:
        return "/"
    result = "/" + joined
    # 拒绝 path segment 为 . 或 ..
    segments = [s for s in result.split("/") if s]
    for seg in segments:
        if seg in (".", ".."):
            raise ValueError(f"invalid path segment {seg!r} in {result!r}")
    return result.rstrip("/") or "/"
