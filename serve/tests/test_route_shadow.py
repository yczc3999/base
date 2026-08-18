"""阶段 3：新 Registry 与合同 fixture 的影子对照测试。"""

import json
from pathlib import Path

import pytest
from fastapi import FastAPI

from app.routes import build_registry, RouteRegistry

FIXTURE = Path(__file__).parent / "fixtures" / "route-catalog-v1.json"
_HTTP = frozenset({"get", "post", "put", "patch", "delete", "options", "head"})


def _snapshot(app_instance):
    spec = app_instance.openapi()
    out = []
    for path in sorted(spec.get("paths", {}).keys()):
        for method, detail in sorted(spec["paths"][path].items()):
            if method.lower() not in _HTTP:
                continue
            out.append(
                {
                    "path": path,
                    "method": method.upper(),
                    "operationId": detail.get("operationId"),
                    "tags": detail.get("tags", []),
                    "responses": dict(detail.get("responses", {})),
                }
            )
    return out


@pytest.fixture(scope="module")
def registry_app():
    registry = build_registry()
    registry.validate()
    app = FastAPI()
    registry.install(app)
    return app


def test_registry_installs_159_operations(registry_app):
    snapshot = _snapshot(registry_app)
    assert len(snapshot) == 159


def test_registry_matches_fixture_zero_diff(registry_app):
    with open(FIXTURE, encoding="utf-8") as f:
        expected = json.load(f)
    assert _snapshot(registry_app) == expected


def test_fallback_is_last_http_route():
    registry = build_registry()
    specs = list(registry.specs())
    normal = [s for s in specs if s.priority.value == "normal"]
    fallback = [s for s in specs if s.priority.value == "fallback"]
    assert fallback and fallback[0].path == "/{name}"
    assert all(s.path != "/{name}" for s in normal)