"""AI 成功 cache：exact content-addressed；失败不缓存（WP-02 Checkpoint B）。

cache key 至少包含 role、全部 input manifest hashes、provider/route/model、prompt/schema/code
version、network/tool policy、sampling（任务 §5.6）。只缓存 ``ACCEPTED + network=NONE``。
cache hit 仍生成新 invocation（cost=0，引用 source invocation）。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.trading.hashing import canonical_hash

CACHE_SCOPE = "ai_cache"
# 只允许缓存 network=NONE 的 ACCEPTED 结果
_CACHE_NETWORK_NONE = "NONE"


def cache_key(
    *,
    role: str,
    input_manifest_hash: str,
    provider: str,
    route: str,
    model: str,
    prompt_hash: str,
    schema_hash: str,
    code_hash: str,
    network_policy: str,
    tools: list[str] | None,
    domains: list[str] | None,
    sampling: dict,
    seed: int | None,
    effort: str | None,
    max_tokens: int | None,
) -> str:
    """exact cache key；输入顺序无关（内部排序）。"""
    return canonical_hash(
        {
            "role": role,
            "input_manifest_hash": input_manifest_hash,
            "provider": provider,
            "route": route,
            "model": model,
            "prompt_hash": prompt_hash,
            "schema_hash": schema_hash,
            "code_hash": code_hash,
            "network_policy": network_policy,
            "tools": sorted(tools or []),
            "domains": sorted(domains or []),
            "sampling": sampling,
            "seed": seed,
            "effort": effort,
            "max_tokens": max_tokens,
        }
    )


def cacheable(accepted: bool, network_policy: str) -> bool:
    """只缓存 ACCEPTED + network=NONE。"""
    return bool(accepted) and network_policy == _CACHE_NETWORK_NONE


@dataclass(frozen=True)
class CacheHit:
    hit: bool
    cache_key: str | None = None
    source_invocation_id: int | None = None
    cached_output: dict | None = None
