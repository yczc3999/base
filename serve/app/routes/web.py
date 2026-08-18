"""
Web / SEO 路由 — sitemap / robots / indexnow 根兜底

- `/sitemap.xml` / `/sitemap-{n}.xml` / `/robots.txt`：爬虫端点，Public
- `/{name}`：IndexNow key 文件，必须使用 `routes.fallback(...)`，
  自动标记 FALLBACK 并放在所有普通 HTTP 路由与 Mount 之后。

`/{name}` 使用 fallback() 显式声明，不再依赖 main.py 手工注册顺序
（设计文档 §4.3 / §7.5）。
"""

from __future__ import annotations

from app.controllers.web import seo as web_seo
from app.routes.registry import RouteRegistry
from app.routes.types import RouteAccess


def register_web_routes(routes: RouteRegistry) -> None:
    web = routes.group(name="web.", access=RouteAccess.PUBLIC)

    web.get("/sitemap.xml", web_seo.sitemap_index).name("sitemap.index")
    web.get("/sitemap-{n}.xml", web_seo.sitemap_chunk).name("sitemap.chunk")
    web.get("/robots.txt", web_seo.robots).name("robots")
    web.fallback("/{name}", web_seo.indexnow_key_file).name("indexnow.key_file")