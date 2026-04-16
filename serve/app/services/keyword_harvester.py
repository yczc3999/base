"""SEO 关键词采集器 — Suggest API 递归扩展.

策略: 种子 → Suggest API 拿补全 → 补全再作种子递归 → 去重

支持引擎 (engine_map 第二位是 source_code 字符串, 与 DB keywords.source_code 对齐):
    google / duckduckgo / yandex      (西方)
    baidu / sogou                     (国内, 对汽车/VIN 等业务效果更好)

Google Suggest 是 Chrome 地址栏的自动补全接口, 稳定不被反爬, 优于抓搜索结果页.
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
    """Google 自动补全 (Chrome Suggest API)."""
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
    """DuckDuckGo 自动补全."""
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
    """Yandex 自动补全."""
    url = f"https://suggest.yandex.com/suggest-ff.cgi?part={quote_plus(seed)}&uil=en&lid=84"
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(url, headers=_headers())
            data = json.loads(r.text)
            return data[1] if len(data) > 1 else []
    except Exception as e:
        logger.warning("yandex suggest '%s': %s", seed, e)
        return []


async def suggest_baidu(seed: str) -> list[str]:
    """百度下拉 — opensearch 格式: [seed, [suggestions...]]."""
    url = (
        f"https://suggestion.baidu.com/su"
        f"?wd={quote_plus(seed)}&action=opensearch"
    )
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(url, headers=_headers())
            data = json.loads(r.text)
            return data[1] if len(data) > 1 and isinstance(data[1], list) else []
    except Exception as e:
        logger.warning("baidu suggest '%s': %s", seed, e)
        return []


async def suggest_sogou(seed: str) -> list[str]:
    """搜狗下拉 — suggnew/ajajjson 接口,返回 JSON 数组第二位是补全."""
    url = (
        f"https://www.sogou.com/suggnew/ajajjson"
        f"?type=web&key={quote_plus(seed)}"
    )
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(url, headers=_headers())
            txt = r.text.strip()
            start = txt.find("[")
            end = txt.rfind("]")
            if start == -1 or end == -1:
                return []
            data = json.loads(txt[start : end + 1])
            return data[1] if len(data) > 1 and isinstance(data[1], list) else []
    except Exception as e:
        logger.warning("sogou suggest '%s': %s", seed, e)
        return []


# engine_name → (fetch_func, source_code 字符串), source_code 写入 keywords.source_code
ENGINE_MAP: dict[str, tuple] = {
    "google":     (suggest_google,     "google"),
    "duckduckgo": (suggest_duckduckgo, "ddg"),
    "yandex":     (suggest_yandex,     "yandex"),
    "baidu":      (suggest_baidu,      "baidu"),
    "sogou":      (suggest_sogou,      "sogou"),
}


async def harvest_recursive(
    seeds: list[str],
    engines: list[str] | None = None,
    depth: int = 2,
    max_per_level: int = 20,
    max_total: int = 200,
    delay_sec: float = 1.0,
) -> list[dict]:
    """递归采集. 返回 [{keyword, source_code, seed_keyword}, ...].

    engines 默认 ["baidu", "google", "duckduckgo"] — 国内业务优先百度.
    """
    if engines is None:
        engines = ["baidu", "google", "duckduckgo"]

    seen: set[str] = set()
    results: list[dict] = []
    current_seeds = list(seeds)

    for level in range(depth):
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

                func_src = ENGINE_MAP.get(engine_name)
                if not func_src:
                    continue
                func, source_code = func_src

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
                        "source_code": source_code,
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
