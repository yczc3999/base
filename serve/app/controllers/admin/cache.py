"""缓存管理 — 查看各模块缓存 key 数 + 一键清理.

缓存前缀从 app.logics 下所有 BaseLogic 实例动态发现（自动跟踪新模块）。
settings 模块用自定义 key (settings:all), 单独兜底清理。
"""
import importlib
import inspect
import pkgutil

from fastapi import APIRouter, Request, Depends

from app.deps import AuthInfo, require_perms
from app.logics.base import BaseLogic
from app.services.redis import get_redis, cache_del_pattern
from app.utils.response import ok, fail

router = APIRouter()

_perm_stats = require_perms("admin:cache:stats")
_perm_clear = require_perms("admin:cache:clear")


def _discover_cache_modules() -> list[dict]:
    """扫描 app.logics 下所有 BaseLogic 实例的 cache_prefix."""
    import app.logics as logics_pkg

    modules = {}
    for mod_info in pkgutil.iter_modules(logics_pkg.__path__):
        if mod_info.name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"app.logics.{mod_info.name}")
        except Exception:
            continue
        for name, obj in inspect.getmembers(mod):
            if isinstance(obj, BaseLogic) and getattr(obj, "cache_prefix", ""):
                modules.setdefault(obj.cache_prefix, name)
    # 固定补充: settings 的聚合缓存 key
    result = [{"prefix": p, "label": n.removesuffix("Logic")} for p, n in modules.items()]
    return sorted(result, key=lambda x: x["prefix"])


def _match_patterns(prefix: str) -> list[str]:
    """清理/统计用匹配模式 (兼容 settings 聚合 key + dict_item 的 items 缓存)."""
    if prefix == "setting":
        return ["setting:*", "settings:*"]
    if prefix == "dict_item":
        # L5 修复: dict_item 变更会失效 dict:items:{type} 缓存, 手动清理需一并覆盖
        return ["dict_item:*", "dict:items:*"]
    return [f"{prefix}:*"]


async def _count_keys(r, pattern: str) -> int:
    cursor = 0
    count = 0
    while True:
        cursor, keys = await r.scan(cursor, match=pattern, count=500)
        count += len(keys)
        if cursor == 0:
            break
    return count


@router.get("/cache/stats")
async def cache_stats(auth: AuthInfo = Depends(_perm_stats)):
    """各模块缓存 key 数 + Redis dbsize."""
    r = await get_redis()
    try:
        dbsize = await r.dbsize()
    except AttributeError:
        dbsize = 0

    modules = []
    for mod in _discover_cache_modules():
        total = 0
        for pattern in _match_patterns(mod["prefix"]):
            total += await _count_keys(r, pattern)
        modules.append({**mod, "keys": total})

    return ok({"dbsize": dbsize, "modules": modules})


@router.post("/cache/clear")
async def cache_clear(request: Request, auth: AuthInfo = Depends(_perm_clear)):
    """清空指定模块缓存（prefix 白名单校验, 防任意删除）. """
    body = await request.json()
    prefix = body.get("prefix", "")
    if not prefix:
        return fail("缺少 prefix")

    allowed = {m["prefix"] for m in _discover_cache_modules()}
    if prefix not in allowed:
        return fail("不支持的缓存模块")

    cleared = 0
    for pattern in _match_patterns(prefix):
        await cache_del_pattern(pattern)
    return ok({"cleared": prefix})
