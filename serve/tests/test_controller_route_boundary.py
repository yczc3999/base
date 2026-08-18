"""阶段 4：Controller 路由边界 AST 测试

用 AST 扫描 `serve/app/controllers/**/*.py`，发现以下任一项即失败：

- APIRouter import
- APIRouter(...) call
- @router.* decorator
- include_router(...) call

过渡兼容文件 `controllers/base.py` 只允许 re-export，不得自行构造 Router。
"""

import ast
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).parent.parent
CONTROLLERS_DIR = BASE_DIR / "app" / "controllers"

# 允许保留路由构造的兼容文件（阶段 4 后仅 base.py）
_ALLOWED_ROUTER_FILES = {
    "base.py",  # 仅为 re-export 兼容层
}


def _iter_py_files(root: Path):
    for p in sorted(root.rglob("*.py")):
        if p.name == "__init__.py":
            continue
        yield p


def _find_violations(path: Path):
    """返回 (line, kind, detail) 列表。"""
    violations = []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return [("parse", "syntax error", path)]

    for node in ast.walk(tree):
        # APIRouter import
        if isinstance(node, ast.ImportFrom):
            if node.module == "fastapi":
                for alias in node.names:
                    if alias.name in ("APIRouter",):
                        violations.append((node.lineno, "import", "APIRouter"))
        # APIRouter(...) / include_router(...)
        if isinstance(node, ast.Call):
            if getattr(node.func, "id", None) == "APIRouter":
                violations.append((node.lineno, "call", "APIRouter(...)"))
            if getattr(node.func, "attr", None) == "include_router":
                violations.append((node.lineno, "call", "include_router(...)"))
        # @router.* 装饰器
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                    if dec.func.attr in (
                        "get", "post", "put", "patch", "delete", "options", "head",
                    ) and getattr(dec.func.value, "id", None) == "router":
                        violations.append((node.lineno, "decorator", f"@{dec.func.attr}"))
                elif isinstance(dec, ast.Attribute):
                    if dec.attr in ("get", "post", "put", "patch", "delete", "options", "head") and getattr(dec.value, "id", None) == "router":
                        violations.append((node.lineno, "decorator", f"@{dec.attr}"))
    return violations


def test_controllers_have_no_router_constructs():
    """全部 Controller 不得包含 Router 构造/路由装饰器/include_router。"""
    all_violations = []
    for p in _iter_py_files(CONTROLLERS_DIR):
        if p.name in _ALLOWED_ROUTER_FILES:
            continue
        violations = _find_violations(p)
        for lineno, kind, detail in violations:
            all_violations.append(f"{p.name}:{lineno} [{kind}] {detail}")
    assert not all_violations, "\n".join(all_violations)
