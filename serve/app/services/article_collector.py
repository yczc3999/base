"""文章采集器 — 搜索 + 页面分析 + 正文提取

分层职责：只负责"从互联网拿内容"，不负责入库和 AI 处理。
入库在 Logic 层，AI 处理在 Controller/Service 层。

正文提取用 trafilatura（业界顶级，支持多语言、自动识别正文区域、
提取发布日期和作者，比手写正则强一个量级）。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import quote_plus

import httpx
import trafilatura

logger = logging.getLogger(__name__)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


@dataclass
class ExtractedArticle:
    title: str
    content: str          # 正文（Markdown 或纯文本）
    url: str
    author: str | None
    date: str | None
    word_count: int
    is_article: bool      # 是否被判定为正式文章（非列表页/论坛等）


async def search(keyword: str, count: int = 10) -> list[SearchResult]:
    """DuckDuckGo 搜索，返回结果列表"""
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(keyword)}"
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
            resp = await c.get(url, headers={"User-Agent": UA})
            results = []
            # DuckDuckGo HTML 版本的结果在 class="result" 块里
            blocks = re.findall(
                r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?'
                r'class="result__snippet"[^>]*>(.*?)</(?:a|span)',
                resp.text, re.DOTALL
            )
            for href, title_html, snippet_html in blocks[:count]:
                title = re.sub(r'<[^>]+>', '', title_html).strip()
                snippet = re.sub(r'<[^>]+>', '', snippet_html).strip()
                if title and href and 'duckduckgo.com' not in href:
                    results.append(SearchResult(title=title, url=href, snippet=snippet))
            logger.info("search '%s' → %d results", keyword, len(results))
            return results
    except Exception as e:
        logger.warning("search failed '%s': %s", keyword, e)
        return []


async def fetch_and_extract(url: str) -> ExtractedArticle | None:
    """抓取页面 + trafilatura 提取正文

    trafilatura 自动处理：
    - 正文区域识别（跳过导航/侧栏/页脚/广告）
    - 段落合并
    - 日期和作者提取
    - 语言检测
    """
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
            resp = await c.get(url, headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            })
            if resp.status_code != 200:
                logger.warning("fetch %s → %d", url, resp.status_code)
                return None
            html = resp.text
    except Exception as e:
        logger.warning("fetch failed %s: %s", url, e)
        return None

    # trafilatura 提取正文
    result = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=False,
        output_format="txt",
    )

    if not result:
        logger.debug("trafilatura returned None for %s", url)
        return None

    # 单独提取元数据（标题/作者/日期）
    title = ""
    date_str = None
    author_str = None
    try:
        meta = trafilatura.metadata.extract_metadata(html)
        if meta:
            title = meta.title or ""
            date_str = str(meta.date) if meta.date else None
            author_str = meta.author if meta.author else None
    except Exception:
        pass

    if not title:
        title_match = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL | re.IGNORECASE)
        title = title_match.group(1).strip() if title_match else ""
        title = re.sub(r'\s*[-|_–—].*$', '', title)

    content = result.strip()
    word_count = len(content)

    # 判断是否为正式文章（正文 > 300 字，段落 > 3 个）
    paragraphs = [p for p in content.split('\n') if len(p.strip()) > 20]
    is_article = word_count > 300 and len(paragraphs) >= 3

    return ExtractedArticle(
        title=title or "无标题",
        content=content,
        url=url,
        author=author_str,
        date=date_str,
        word_count=word_count,
        is_article=is_article,
    )


async def search_and_extract(
    keyword: str,
    max_results: int = 10,
    min_words: int = 300,
) -> list[ExtractedArticle]:
    """搜索 + 逐个抓取 + 过滤非文章页

    返回确认是文章的列表（word_count >= min_words 且 is_article=True）
    """
    results = await search(keyword, count=max_results * 2)  # 多搜一些，过滤后可能不够
    articles = []

    for sr in results:
        if len(articles) >= max_results:
            break
        extracted = await fetch_and_extract(sr.url)
        if not extracted:
            continue
        if not extracted.is_article:
            logger.debug("skip non-article: %s (%d words)", sr.url, extracted.word_count)
            continue
        if extracted.word_count < min_words:
            logger.debug("skip too short: %s (%d words)", sr.url, extracted.word_count)
            continue
        # 用搜索结果的标题兜底（trafilatura 有时拿不到好标题）
        if extracted.title == "无标题" and sr.title:
            extracted.title = sr.title
        articles.append(extracted)

    logger.info("search_and_extract '%s': %d/%d articles", keyword, len(articles), len(results))
    return articles
