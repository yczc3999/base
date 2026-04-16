"""SEO sitemap 服务 —— 增量分片 + 原子写入

策略基准 §4：
- 单文件 ≤ 5000 URL（settings.seo.sitemap_max_per_file）
- 只写 <lastmod>，不写 priority / changefreq（Google 已忽略）
- 索引文件 sitemap.xml 引用所有分片
- 写盘走 .tmp → rename 原子操作（爬虫不会抓到半截文件）

文件落 server/storage/seo/，由 web 控制器 GET /sitemap.xml 等路由读出来。

用法：
    from app.services.seo_sitemap import seo_sitemap_service
    info = await seo_sitemap_service.rebuild(db)
    # info = {"files": [{name, urls, size}, ...], "total_urls": N}
"""
from __future__ import annotations

import os
import math
import datetime as _dt
from pathlib import Path
from xml.sax.saxutils import escape

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.logics.setting import setting_logic


class SeoSitemapService:
    def __init__(self):
        # 落盘根目录
        base = Path(__file__).resolve().parents[2] / "storage" / "seo"
        base.mkdir(parents=True, exist_ok=True)
        self.base_dir = base

    async def _frontend_url(self, db: AsyncSession) -> str:
        """前端 URL，去尾斜杠。"""
        url = (await setting_logic.get(db, "site", "frontend_url", "") or "").rstrip("/")
        return url

    async def _max_per_file(self, db: AsyncSession) -> int:
        v = await setting_logic.get(db, "seo", "sitemap_max_per_file", "5000")
        try:
            return max(int(v), 100)
        except (ValueError, TypeError):
            return 5000

    async def _live_articles(self, db: AsyncSession) -> list[dict]:
        """所有"前台可见"文章：status=1 + 已到发布时间 + 未删除。"""
        r = await db.execute(text(
            "SELECT id, slug, "
            "  GREATEST(updated_at, published_at) AS lastmod "
            "FROM articles "
            "WHERE status = 1 AND deleted_at IS NULL "
            "AND slug IS NOT NULL AND slug <> '' "
            "AND (published_at IS NULL OR published_at <= NOW()) "
            "ORDER BY published_at DESC NULLS LAST, id DESC"
        ))
        return [dict(row) for row in r.mappings().all()]

    def _atomic_write(self, path: Path, content: str) -> None:
        """先写 .tmp 再 rename，POSIX 保证原子。"""
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)

    def _format_lastmod(self, dt) -> str:
        if dt is None:
            return _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S+00:00")
        if isinstance(dt, _dt.datetime):
            return dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        return str(dt)

    def _render_urlset(self, base_url: str, items: list[dict]) -> str:
        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        ]
        for it in items:
            slug = escape(it["slug"])
            loc = f"{base_url}/article/{slug}"
            lastmod = self._format_lastmod(it.get("lastmod"))
            lines.append(f"  <url><loc>{loc}</loc><lastmod>{lastmod}</lastmod></url>")
        lines.append("</urlset>")
        return "\n".join(lines) + "\n"

    def _render_index(self, base_url: str, file_names: list[str], lastmod: str) -> str:
        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        ]
        for name in file_names:
            loc = f"{base_url}/{name}"
            lines.append(f"  <sitemap><loc>{loc}</loc><lastmod>{lastmod}</lastmod></sitemap>")
        lines.append("</sitemapindex>")
        return "\n".join(lines) + "\n"

    async def rebuild(self, db: AsyncSession) -> dict:
        """全量重建（不增量）。N 千篇文章下 < 1s，可接受。"""
        from app.logics.publish_log import publish_log_logic
        import time as _time

        t0 = _time.time()
        base_url = await self._frontend_url(db)
        max_per_file = await self._max_per_file(db)

        if not base_url:
            return {"error": "site.frontend_url 未配置"}

        items = await self._live_articles(db)
        total = len(items)
        n_files = max(1, math.ceil(total / max_per_file))

        file_infos: list[dict] = []
        file_names: list[str] = []

        for i in range(n_files):
            chunk = items[i * max_per_file:(i + 1) * max_per_file]
            name = f"sitemap-{i + 1}.xml"
            content = self._render_urlset(base_url, chunk)
            path = self.base_dir / name
            self._atomic_write(path, content)
            file_names.append(name)
            file_infos.append({"name": name, "urls": len(chunk), "size": path.stat().st_size})

        # 索引文件
        now_str = _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S+00:00")
        idx_content = self._render_index(base_url, file_names, now_str)
        idx_path = self.base_dir / "sitemap.xml"
        self._atomic_write(idx_path, idx_content)

        # robots.txt
        robots = (
            "User-agent: *\n"
            "Disallow: /admin\n"
            "Disallow: /api\n"
            f"Sitemap: {base_url}/sitemap.xml\n"
        )
        self._atomic_write(self.base_dir / "robots.txt", robots)

        elapsed_ms = int((_time.time() - t0) * 1000)
        info = {
            "files": file_infos,
            "total_urls": total,
            "elapsed_ms": elapsed_ms,
            "base_url": base_url,
        }
        await publish_log_logic.write(
            db, action="sitemap_rebuild", level="info",
            msg=f"rebuilt {n_files} files, {total} URLs, {elapsed_ms}ms",
            payload=info,
        )
        await db.commit()
        return info

    def read(self, name: str) -> tuple[str | None, str]:
        """web 控制器读文件。返回 (content, content_type)。"""
        if name not in {"sitemap.xml", "robots.txt"} and not (
            name.startswith("sitemap-") and name.endswith(".xml")
        ):
            return None, ""
        path = self.base_dir / name
        if not path.exists():
            return None, ""
        ct = "application/xml; charset=utf-8" if name.endswith(".xml") else "text/plain; charset=utf-8"
        return path.read_text(encoding="utf-8"), ct


seo_sitemap_service = SeoSitemapService()
