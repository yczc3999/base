"""
python -m app.routes — 路由目录 CLI

用法：
    python -m app.routes list
    python -m app.routes list --scope admin --method POST --contains user
    python -m app.routes json > /tmp/base-routes.json
    python -m app.routes check

- `list`：文本表格，按 PATH, METHODS, ROUTE_ID 稳定排序。
- `json`：JSON 数组，固定 key 顺序，便于 diff。
- `check`：构建 Registry + 全量校验；非零状态失败。
    exit 0  → 校验通过
    exit 1  → 校验失败（打印全部错误）
    exit 2  → 其他错误（Registry 构建失败）
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from app.routes import build_registry
from app.routes.validation import RegistryValidationError


def _registry() -> Any:
    return build_registry()


def _filter_catalog(
    catalog: list[dict[str, Any]],
    *,
    scope: str | None = None,
    method: str | None = None,
    contains: str | None = None,
) -> list[dict[str, Any]]:
    result = catalog
    if scope:
        scope = scope.lower()
        known = {"public", "authenticated", "admin", "client"}
        if scope not in known:
            raise ValueError(f"unknown scope {scope!r}; expected one of {sorted(known)}")
        result = [e for e in result if e["ACCESS"] == scope]
    if method:
        method = method.upper()
        result = [e for e in result if method in e["METHODS"]]
    if contains:
        result = [
            e
            for e in result
            if contains in e["PATH"] or contains in e["ROUTE_ID"]
        ]
    return result


def _format_table(catalog: list[dict[str, Any]]) -> str:
    columns = (
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

    def cell(entry: dict[str, Any], key: str) -> str:
        value = entry[key]
        if key in ("METHODS", "PERMISSIONS", "TAGS"):
            return ",".join(value)
        if key == "MIDDLEWARE":
            return ",".join(value)
        return str(value)

    rows = [[cell(e, c) for c in columns] for e in catalog]
    if not rows:
        return "(no routes)"
    widths = [len(c) for c in columns]
    for row in rows:
        for i, v in enumerate(row):
            widths[i] = max(widths[i], len(v))

    lines = []
    header = "  ".join(c.ljust(widths[i]) for i, c in enumerate(columns))
    lines.append(header)
    lines.append("  ".join("-" * w for w in widths))
    for row in rows:
        lines.append("  ".join(v.ljust(widths[i]) for i, v in enumerate(row)))
    return "\n".join(lines)


def cmd_list(args: argparse.Namespace) -> int:
    registry = build_registry()
    catalog = registry.catalog()
    filtered = _filter_catalog(
        catalog, scope=args.scope, method=args.method, contains=args.contains
    )
    print(_format_table(filtered))
    return 0


def cmd_json(args: argparse.Namespace) -> int:
    registry = build_registry()
    catalog = _filter_catalog(
        registry.catalog(), scope=args.scope, method=args.method, contains=args.contains
    )
    print(json.dumps(catalog, ensure_ascii=False, indent=2, sort_keys=False))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    try:
        registry = build_registry()
        registry.validate()
        n = len(registry.specs())
        m = len(registry.mounts())
        print(f"ok: {n} http routes, {m} mounts")
        return 0
    except RegistryValidationError as e:
        for err in e.errors:
            print(f"ERROR: {err}", file=sys.stderr)
        print(f"check failed: {len(e.errors)} error(s)", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"check error: {e}", file=sys.stderr)
        return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.routes")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="打印路由目录表格")
    p_list.add_argument(
        "--scope",
        default=None,
        choices=("public", "authenticated", "admin", "client"),
        help="按 access scope 过滤：public/authenticated/admin/client",
    )
    p_list.add_argument("--method", default=None, help="按 HTTP Method 过滤，如 POST")
    p_list.add_argument("--contains", default=None, help="按 PATH/ROUTE_ID 子串过滤")
    p_list.set_defaults(func=cmd_list)

    p_json = sub.add_parser("json", help="输出 JSON 路由目录")
    p_json.add_argument(
        "--scope",
        default=None,
        choices=("public", "authenticated", "admin", "client"),
    )
    p_json.add_argument("--method", default=None)
    p_json.add_argument("--contains", default=None)
    p_json.set_defaults(func=cmd_json)

    p_check = sub.add_parser("check", help="校验 Registry 并输出路径/路由数")
    p_check.set_defaults(func=cmd_check)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
