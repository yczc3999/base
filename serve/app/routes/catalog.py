"""
集中式路由注册表 — 标准化目录

`build_catalog(registry)` 输出确定性的全局路由目录，按
(PATH, METHODS, ROUTE_ID) 稳定排序；json 使用固定 key 顺序，便于 diff。
不输出密钥、token 或 runtime 配置值。
"""

from __future__ import annotations

from typing import Any

from fastapi.routing import APIRoute

from app.routes.registry import RouteRegistry

_CATALOG_KEYS = (
    "ROUTE_ID",
    "METHODS",
    "PATH",
    "HANDLER",
    "GROUP",
    "ACCESS",
    "MIDDLEWARE",
    "PERMISSIONS",
    "TAGS",
    "RESPONSE_MODEL",
    "OPERATION_ID",
    "PRIORITY",
    "SOURCE_FILE:LINE",
)

def _handler_name(func: Any) -> str:
    name = getattr(func, "__qualname__", None) or getattr(func, "__name__", None)
    module = getattr(func, "__module__", None)
    if module and name:
        return f"{module}.{name}"
    return str(name or func)


def _middleware_names(middleware: tuple[Any, ...]) -> list[str]:
    out = []
    for m in middleware:
        name = getattr(m, "__qualname__", None) or getattr(m, "__name__", None)
        out.append(name or str(m))
    return out


def _route_entry(spec: Any, effective_operation_id: str) -> dict[str, Any]:
    return {
        "ROUTE_ID": spec.route_id,
        "METHODS": list(spec.methods),
        "PATH": spec.path,
        "HANDLER": _handler_name(spec.endpoint),
        "GROUP": spec.group_path or "",
        "ACCESS": spec.access.value,
        "MIDDLEWARE": _middleware_names(spec.middleware),
        "PERMISSIONS": list(spec.permissions),
        "TAGS": list(spec.tags),
        "RESPONSE_MODEL": getattr(spec.response_model, "__name__", None),
        "OPERATION_ID": effective_operation_id,
        "PRIORITY": spec.priority.value,
        "SOURCE_FILE:LINE": f"{spec.source_file}:{spec.source_line}",
    }


def build_catalog(registry: RouteRegistry) -> list[dict[str, Any]]:
    """构建标准化路由目录（含 HTTP 路由与 Mount）。"""
    # 从实际编译后的 APIRoute 取 unique_id，保证 Catalog 展示
    # FastAPI 真正使用的 operationId，而不是未设置时的空值。
    effective_operation_ids: dict[tuple[str, frozenset[str]], str] = {}
    compiled = registry.compile_http_router(include_fallback=True)
    for route in compiled.routes:
        if isinstance(route, APIRoute):
            key = (route.path, frozenset(route.methods or ()))
            effective_operation_ids[key] = route.unique_id

    entries: list[dict[str, Any]] = []
    for spec in registry.specs():
        key = (spec.path, frozenset(spec.methods))
        operation_id = effective_operation_ids.get(key, spec.operation_id or "")
        entries.append(_route_entry(spec, operation_id))
    for mount in registry.mounts():
        entries.append(
            {
                "ROUTE_ID": f"mount.{mount.name}",
                "METHODS": ["MOUNT"],
                "PATH": mount.path,
                "HANDLER": _handler_name(getattr(mount.app, "__class__", mount.app)),
                "GROUP": mount.group_path or "",
                "ACCESS": mount.access.value,
                "MIDDLEWARE": [],
                "PERMISSIONS": [],
                "TAGS": [],
                "RESPONSE_MODEL": None,
                "OPERATION_ID": "mount",
                "PRIORITY": "mount",
                "SOURCE_FILE:LINE": f"{mount.source_file}:{mount.source_line}",
            }
        )

    def sort_key(entry: dict[str, Any]) -> tuple:
        return (
            entry["PATH"],
            tuple(entry["METHODS"]),
            entry["ROUTE_ID"],
        )

    stable = sorted(entries, key=sort_key)
    # 固定 key 顺序便于 diff
    return [{k: e[k] for k in _CATALOG_KEYS} for e in stable]


def catalog_sorted_keys() -> tuple[str, ...]:
    return _CATALOG_KEYS
