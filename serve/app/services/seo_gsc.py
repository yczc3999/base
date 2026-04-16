"""Google Search Console 第三方服务（Phase 2 占位）

接口已定，body 暂为 NotImplemented，不影响 Phase 1 上线。
未来填入 google-api-python-client + service account 凭据即可。

凭据来源：settings.seo_creds.gsc_service_account_json（加密存）
用途：拉 indexed_cnt（已索引页面数），用于 SeoPhaseService 的 health_score 计算。

用法（Phase 2 实装后）：
    from app.services.seo_gsc import gsc_service
    n = await gsc_service.indexed_count(db)
    if gsc_service.ok:
        ...
"""
from __future__ import annotations

from app.services.base import BaseService


class GscService(BaseService):
    category = "seo_creds"

    async def indexed_count(self, db) -> int | None:
        """返回当前站点的 Google 已索引页面数。Phase 2 实装。"""
        self._reset()
        # TODO Phase 2: 用 service account JSON 调 Search Console API
        # from googleapiclient.discovery import build
        # from google.oauth2 import service_account
        # 然后调 searchanalytics.query 或 sites/{siteUrl}/sitemaps/{sitemap_url} 推断
        return self._fail("GscService Phase 2 尚未实装")


gsc_service = GscService()
