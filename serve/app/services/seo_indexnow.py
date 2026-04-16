"""IndexNow 第三方服务 —— Bing / Yandex / Seznam 即时收录通知

继承 BaseService 模式：错误存实例不抛异常。

策略基准 §5：
- key 已配 → 自动启用；key 空 → 服务返回 ok=True 但 noop（"未配置则跳过"，不算失败）
- 单 URL 10 分钟冷却（由调用方查 publish_log 实现）
- 日 500 URL 配额（由调用方维护）
- POST https://api.indexnow.org/IndexNow

key 校验文件路由 GET /{key}.txt → 由 web/seo.py 控制器动态返回 key 本身。

用法：
    from app.services.seo_indexnow import indexnow_service
    await indexnow_service.ping(db, urls=[full_url1, full_url2])
    if indexnow_service.failed:
        # 写 publish_log warn，不阻塞主流程
        ...
"""
from __future__ import annotations

from urllib.parse import urlparse

import httpx

from app.services.base import BaseService


class IndexNowService(BaseService):
    category = "seo_creds"
    ENDPOINT = "https://api.indexnow.org/IndexNow"
    DEFAULT_TIMEOUT = 10

    async def _load_key_and_host(self, db) -> tuple[str, str] | None:
        """从 settings 拿 key 和 host（host 来自 site.frontend_url）。"""
        from app.logics.setting import setting_logic
        key = (await setting_logic.get(db, "seo_creds", "indexnow_key", "") or "").strip()
        url = (await setting_logic.get(db, "site", "frontend_url", "") or "").strip()
        if not key or not url:
            return None
        # 提 host（去协议 + 去尾斜杠）
        host = url.replace("https://", "").replace("http://", "").rstrip("/").split("/")[0]
        return key, host

    async def ping(self, db, urls: list[str]) -> dict | None:
        """提交 URL 给 IndexNow。
        urls: 完整绝对路径的字符串列表。
        返回 None 表示未配置（不算失败）；返回 dict 表示请求已发出。
        """
        self._reset()
        if not urls:
            return self._succeed({"submitted": 0, "skipped": 0, "noop": True})

        loaded = await self._load_key_and_host(db)
        if not loaded:
            # 未配置 key 或 frontend_url —— 跳过，不算失败
            return self._succeed({"submitted": 0, "skipped": len(urls), "reason": "indexnow_key/frontend_url 未配置"})

        key, host = loaded

        # 协议要求所有 URL 同 host —— 严校验：urlparse.hostname 完全匹配
        host_lower = host.lower()
        same_host = [u for u in urls if (urlparse(u).hostname or "").lower() == host_lower]
        if not same_host:
            return self._fail(f"URLs 未匹配 host={host}")

        body = {
            "host": host,
            "key": key,
            "keyLocation": f"https://{host}/{key}.txt",
            "urlList": same_host,
        }

        try:
            async with httpx.AsyncClient(timeout=self.DEFAULT_TIMEOUT) as client:
                resp = await client.post(self.ENDPOINT, json=body)
            # IndexNow 规范：200/202 = OK，422 = key 错，429 = rate limit
            if resp.status_code in (200, 202):
                return self._succeed({"submitted": len(same_host), "status": resp.status_code})
            return self._fail(f"IndexNow HTTP {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            return self._fail(f"IndexNow 请求异常: {e}")


indexnow_service = IndexNowService()
