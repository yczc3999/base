"""
阶段 0：冻结可观测路由契约

- `snapshot_openapi_routes(app)`：从 `app.openapi()["paths"]` 提取
  Method、path、operationId、tags、response schema。
- `test_current_route_contract_snapshot()`：确认当前 159 operations
  与 `tests/fixtures/route-catalog-v1.json` 完全相等。

fixture 由 `serve/app/main.py` 当前路由在 2026-08-17 生成，
作为集中式路由注册表重构（serve/docs/route-registry-design.md）
的不可变契约基线。任何 URL/Method/operationId/tags/response schema
变更都必须先显式更新 fixture，再通过 Registry 引入。
"""

import json
from pathlib import Path

from app.main import app
from app.routes import build_registry

# 与 openapi 文档中 operation 对应的 HTTP 方法
_HTTP_METHODS = frozenset(
    {"get", "post", "put", "patch", "delete", "options", "head"}
)

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "route-catalog-v1.json"


def snapshot_openapi_routes(app_instance):
    """从 app.openapi() 提取可观测路由契约。

    返回按 (path, method) 稳定排序的列表，每个元素包含：
    - path
    - method
    - operationId
    - tags
    - responses

    URL 模板中 FastAPI 路径参数（{param}）保持原样，
    responses 只保留 status code → {} 的映射结构（不含内容细节）。
    """
    spec = app_instance.openapi()
    snapshot = []
    for path in sorted(spec.get("paths", {}).keys()):
        for method, detail in sorted(spec["paths"][path].items()):
            if method.lower() not in _HTTP_METHODS:
                continue
            snapshot.append(
                {
                    "path": path,
                    "method": method.upper(),
                    "operationId": detail.get("operationId"),
                    "tags": detail.get("tags", []),
                    "responses": dict(detail.get("responses", {})),
                }
            )
    return snapshot


def _load_fixture():
    with open(_FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


def test_fixture_has_159_operations():
    """基线 fixture 必须固定为 159 条 operation（159 个 path）。"""
    fixture = _load_fixture()
    assert len(fixture) == 159
    assert len({entry["path"] for entry in fixture}) == 159


def test_current_route_contract_snapshot():
    """当前 app 路由与基线 fixture 完全相等。"""
    current = snapshot_openapi_routes(app)
    fixture = _load_fixture()
    assert current == fixture


def test_snapshot_is_stable_sorted():
    """快照必须按 (path, method) 稳定排序，保证两次运行字节级可比较。"""
    snapshot = snapshot_openapi_routes(app)
    keys = [(entry["path"], entry["method"]) for entry in snapshot]
    assert keys == sorted(keys)


def test_method_distribution_matches_checkpoint():
    """审计记录：GET 70、POST 89。"""
    fixture = _load_fixture()
    by_method: dict[str, int] = {}
    for entry in fixture:
        by_method[entry["method"]] = by_method.get(entry["method"], 0) + 1
    assert by_method.get("GET") == 70
    assert by_method.get("POST") == 89
    assert sum(by_method.values()) == 159


def test_public_admin_client_policy_baseline():
    """Public/Admin/Client 策略清单（Registry 元数据对照基线）。

    - /api/dict/* 与根路径 SEO 端点为 Public、无中间件。
    - /api/admin/* 为 Admin（含 login/captcha/refreshToken/site 公开端点）。
    - /api/client/* 为 Client（含 login/register/refreshToken 公开端点）。
    - /health* 为系统端点，可公开访问。
    """
    fixture = _load_fixture()

    admin_paths = {e["path"] for e in fixture if e["path"].startswith("/api/admin/")}
    client_paths = {e["path"] for e in fixture if e["path"].startswith("/api/client/")}
    public_paths = {e["path"] for e in fixture if e["path"].startswith("/api/dict/")}
    private_file_paths = {e["path"] for e in fixture if e["path"].startswith("/api/file/")}
    system_paths = {e["path"] for e in fixture if e["path"].startswith("/health")}
    root_seo_paths = {
        e["path"] for e in fixture
        if e["path"] in ("/sitemap.xml", "/robots.txt") or e["path"].startswith("/sitemap-")
    }

    # 人工列举的公开端点，防止意外将敏感端点挪进 Public
    assert {"/api/admin/user/captcha", "/api/admin/user/login", "/api/admin/user/refreshToken", "/api/admin/setting/site"} <= admin_paths
    assert "/api/client/user/login" in client_paths
    assert "/api/client/user/register" in client_paths
    assert "/api/client/user/refreshToken" in client_paths

    # 根兜底 {name} 是 indexnow key 文件，属于公开 SEO 端点
    fallback = {e["path"] for e in fixture if e["path"] == "/{name}"}
    assert fallback == {"/{name}"}

    # 隐私文件代理端点（/api 前缀 + /file/{file_id}，require_admin 保护）
    assert private_file_paths == {"/api/file/{file_id}"}

    # 系统/公开端点必须存在且数量与快照一致
    assert len(system_paths) == 3
    assert len(root_seo_paths) == 3
    assert len(public_paths) >= 1
    assert len(admin_paths) + len(client_paths) + len(public_paths) + len(private_file_paths) + len(system_paths) + len(root_seo_paths) + 1 == 159  # +1 为 /{name}


def test_all_operation_ids_unique():
    """operationId 必须全局唯一（校验器 v1 的编译前前提）。"""
    fixture = _load_fixture()
    ids = [e["operationId"] for e in fixture]
    assert len(ids) == len(set(ids))
    assert all(x is not None for x in ids)


def test_registry_catalog_matches_effective_openapi_operation_ids():
    """Catalog 必须展示 FastAPI 最终采用的 operationId，而不是空占位。"""
    expected = {
        (entry["path"], entry["method"]): entry["operationId"]
        for entry in snapshot_openapi_routes(app)
    }
    catalog = [
        entry
        for entry in build_registry().catalog()
        if entry["METHODS"] != ["MOUNT"]
    ]

    actual = {}
    for entry in catalog:
        for method in entry["METHODS"]:
            actual[(entry["PATH"], method)] = entry["OPERATION_ID"]

    assert actual == expected
    assert all(entry["OPERATION_ID"] for entry in catalog)


def test_registry_catalog_sources_point_to_authoritative_manifests():
    """来源必须精确落在 Manifest 文件，不能退化为 runpy/stdin 调用帧。"""
    catalog = build_registry().catalog()
    assert all(entry["SOURCE_FILE:LINE"].startswith("app/routes/") for entry in catalog)
    assert all("<stdin>" not in entry["SOURCE_FILE:LINE"] for entry in catalog)
    assert all("<frozen runpy>" not in entry["SOURCE_FILE:LINE"] for entry in catalog)
