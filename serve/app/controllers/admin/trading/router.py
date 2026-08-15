"""V2 Admin Read API 路由聚合（WP-07A Checkpoint A）。

只 include 一次；旧 ``/api/admin/trading/runtime`` 由 ``runtime`` 子模块保持兼容。
所有 V2 读端点使用 ``v2:*:view`` 权限（AND 语义由 ``require_all_perms`` 提供）。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.controllers.admin.trading import (
    ai_invocations,
    artifacts,
    components,
    costs,
    dashboard,
    decisions,
    episodes,
    evaluation,
    execution,
    integrity,
    markets,
    model_routes,
    releases,
    replay,
    runtime,
    runtime_config,
    strategy_config,
    tags,
)

router = APIRouter()



# 旧路由兼容

# 旧路由兼容：/api/admin/trading/runtime（原 trading.py 迁移到 runtime.py）
router.include_router(runtime.router)

# V2 Admin Read endpoints
router.include_router(dashboard.router)
router.include_router(markets.router)
router.include_router(tags.router)
router.include_router(components.router)
router.include_router(episodes.router)
router.include_router(decisions.router)
router.include_router(execution.router)
router.include_router(model_routes.router)
router.include_router(ai_invocations.router)
router.include_router(costs.router)
router.include_router(strategy_config.router)
router.include_router(releases.router)
router.include_router(evaluation.router)
router.include_router(replay.router)
router.include_router(integrity.router)
router.include_router(artifacts.router)
router.include_router(runtime_config.router)
