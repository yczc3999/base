"""Bing Webmaster 第三方服务（Phase 2 占位）

GSC 不可用时的备用收录数据源。

凭据：settings.seo_creds.bing_api_key
端点：https://ssl.bing.com/webmaster/api.svc/json/

用法（Phase 2 实装后）：
    from app.services.seo_bing import bing_service
    n = await bing_service.indexed_count(db)
"""
from __future__ import annotations

from app.services.base import BaseService


class BingService(BaseService):
    category = "seo_creds"

    async def indexed_count(self, db) -> int | None:
        """Bing 已索引页面数。Phase 2 实装。"""
        self._reset()
        # TODO Phase 2: GET /GetUrlInfo?siteUrl=...&apikey=...
        return self._fail("BingService Phase 2 尚未实装")


bing_service = BingService()
