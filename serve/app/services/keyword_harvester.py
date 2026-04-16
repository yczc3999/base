"""SEO 关键词采集器 — Google Suggest + DuckDuckGo Suggest 递归扩展

策略：种子关键词 → Suggest API 拿补全 → 补全结果再作为种子递归 → 去重入库

Google Suggest API 是 Chrome 地址栏的自动补全接口，稳定可用、不被反爬。
比抓 Google 搜索结果页底部"相关搜索"更可靠。
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from urllib.parse import quote_plus

import httpx

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]


def _headers() -> dict:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }


async def suggest_google(seed: str, hl: str = "zh-CN") -> list[str]:
    """Google 自动补全建议（Chrome Suggest API）"""
    url = (
        f"https://suggestqueries.google.com/complete/search"
        f"?client=chrome&q={quote_plus(seed)}&hl={hl}"
    )
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(url, headers=_headers())
            data = json.loads(r.text)
            return data[1] if len(data) > 1 else []
    except Exception as e:
        logger.warning("google suggest '%s': %s", seed, e)
        return []


async def suggest_duckduckgo(seed: str) -> list[str]:
    """DuckDuckGo 自动补全建议"""
    url = f"https://duckduckgo.com/ac/?q={quote_plus(seed)}&type=list"
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(url, headers=_headers())
            data = json.loads(r.text)
            if isinstance(data, list) and data and isinstance(data[0], dict):
                return [item["phrase"] for item in data if "phrase" in item]
            if isinstance(data, list) and len(data) > 1:
                return data[1] if isinstance(data[1], list) else []
            return []
    except Exception as e:
        logger.warning("ddg suggest '%s': %s", seed, e)
        return []


async def suggest_yandex(seed: str) -> list[str]:
    """Yandex 自动补全建议"""
    url = f"https://suggest.yandex.com/suggest-ff.cgi?part={quote_plus(seed)}&uil=en&lid=84"
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(url, headers=_headers())
            data = json.loads(r.text)
            return data[1] if len(data) > 1 else []
    except Exception as e:
        logger.warning("yandex suggest '%s': %s", seed, e)
        return []


async def harvest_recursive(
    seeds: list[str],
    engines: list[str] | None = None,
    depth: int = 2,
    max_per_level: int = 20,
    max_total: int = 200,
    delay_sec: float = 1.0,
) -> list[dict]:
    """递归采集，带层数 + 每层数量 + 总量三重限制。

    参数：
        seeds: 初始种子关键词
        engines: 搜索引擎列表
        depth: 最大递归层数（1=只采种子的直接补全，2=补全的补全，以此类推）
        max_per_level: 每层最多处理多少个种子词（控制扇出，避免指数爆炸）
        max_total: 总共最多采集多少个新关键词（达到后立即停止）
        delay_sec: 每次请求之间的基础延迟（±50% 抖动）

    扇出估算（Google Suggest 通常返回 5-10 个）：
        depth=1, max_per_level=20 → 最多 ~200 个
        depth=2, max_per_level=10 → 最多 ~10×10×8 = ~800 个（受 max_total 限制）
        depth=3, max_per_level=5  → 最多 ~5×5×5×8 = ~1000 个（受 max_total 限制）

    返回 [{"keyword": str, "source": int, "seed_keyword": str}, ...]
    """
    if engines is None:
        engines = ["google", "duckduckgo"]

    seen: set[str] = set()
    results: list[dict] = []
    current_seeds = list(seeds)

    engine_map = {
        "google": (suggest_google, 1),
        "duckduckgo": (suggest_duckduckgo, 3),
        "yandex": (suggest_yandex, 2),
    }

    for level in range(depth):
        # 每层只取前 max_per_level 个种子
        batch = current_seeds[:max_per_level]
        next_seeds: list[str] = []
        logger.info(
            "harvest level %d: %d seeds (capped from %d), total so far: %d/%d",
            level, len(batch), len(current_seeds), len(results), max_total,
        )

        for seed in batch:
            if len(results) >= max_total:
                logger.info("reached max_total=%d, stopping", max_total)
                break

            seed_lower = seed.lower().strip()
            if seed_lower in seen:
                continue
            seen.add(seed_lower)

            for engine_name in engines:
                if len(results) >= max_total:
                    break

                func, source = engine_map.get(engine_name, (None, 0))
                if not func:
                    continue

                suggestions = await func(seed)
                for kw in suggestions:
                    if len(results) >= max_total:
                        break
                    kw_clean = kw.strip()
                    kw_lower = kw_clean.lower()
                    if kw_lower in seen or not kw_clean or len(kw_clean) < 3:
                        continue
                    seen.add(kw_lower)
                    results.append({
                        "keyword": kw_clean,
                        "source": source,
                        "seed_keyword": seed,
                    })
                    next_seeds.append(kw_clean)

                jitter = delay_sec * (0.5 + random.random())
                await asyncio.sleep(jitter)

        if len(results) >= max_total:
            break
        current_seeds = next_seeds
        if not current_seeds:
            break

    logger.info(
        "harvest done: %d keywords from %d seeds, depth=%d, limits=(%d/level, %d total)",
        len(results), len(seeds), depth, max_per_level, max_total,
    )
    return results


# 向后兼容
harvest_all = harvest_recursive
