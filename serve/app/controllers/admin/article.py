"""文章管理 — CRUD only

完整版（含 AI 生成 / 采集 / SEO 排期触发等业务端点）需要 ai_content /
article_collector / tag_logic / publish_log_logic 等依赖。这些模块在 base
框架中按需安装：
- 装 AI 模块 → /admin/article/ai-generate / ai-rewrite-stream
- 装采集模块 → /admin/article/collect-stream / collect-stats
- 装 SEO 模块 → /admin/article/gen-from-tags-stream

参考 gui-tu/server/app/controllers/admin/article.py 完整实现。

时间字段约定（前台展示/排序一律用 published_at，created_at 仅作审计元数据）：
- published_at  ← 对外"发布时间"，可编辑（前端底栏可填任意过去/未来时间）；
                  status=1 且 published_at 为空时由 article_logic.before_create/before_edit
                  自动填 NOW()
- created_at    ← 入库时间，不可编辑
- updated_at    ← 更新时间，ORM 自动维护
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.controllers.base import crud_router
from app.deps import require_admin
from app.logics.article import article_logic
from app.services.database import get_db
from app.utils.response import ok

router = APIRouter()

router.include_router(
    crud_router("article", article_logic, tags=["admin-article"],
                auth_dep=require_admin, perms_prefix="admin:article")
)


# ---- 采集统计（轻量，不依赖任何 SEO 模块）----

@router.get("/article/collect-stats", tags=["admin-article"])
async def collect_stats(_=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return ok(await article_logic.collect_stats(db))
