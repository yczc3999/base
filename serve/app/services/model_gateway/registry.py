"""Provider/model/route allowlist registry（WP-02 Checkpoint B）。

只按冻结 binding 构造 Driver；禁止字符串任意 import/model（任务 §5.1/§5.3）。
"""

from __future__ import annotations

from dataclasses import dataclass

# provider → 可用 route → 可用 model 集合（首版注册表；Anthropic/OpenAI 不注册）
_ROUTES: dict[str, dict[str, frozenset[str]]] = {
    "deepseek": {
        "direct": frozenset({"deepseek-v4-pro", "deepseek-v4"}),
        "relay": frozenset({"deepseek-v4-pro"}),
    },
    "xai": {
        "direct": frozenset({"grok-4.5"}),
        "relay": frozenset({"grok-4.5-build"}),
    },
    "gemini": {
        "direct": frozenset({"gemini-3.6-flash"}),
        "relay": frozenset({"gemini-3.6-flash-relay"}),
    },
    "kimi": {
        "direct": frozenset({"kimi-k3"}),
        "relay": frozenset({"kimi-k3-relay"}),
    },
    "packy": {
        "direct": frozenset({"packy-preview"}),
    },
}


@dataclass(frozen=True)
class RouteModel:
    provider: str
    route: str
    model: str


def resolve(provider: str, route: str, model: str) -> RouteModel:
    """allowlist 校验 requested provider/route/model；未注册直接 ValueError。"""
    routes = _ROUTES.get(provider)
    if routes is None:
        raise ValueError(f"model_provider_not_allowed:{provider}")
    models = routes.get(route)
    if models is None:
        raise ValueError(f"model_route_not_allowed:{provider}:{route}")
    if model not in models:
        raise ValueError(f"model_not_allowed:{provider}:{route}:{model}")
    return RouteModel(provider=provider, route=route, model=model)


def assert_returned_model(provider: str, model: str) -> None:
    """返回的 provider/model 必须 allowlist（relay alias 漂移直接 REJECTED）。"""
    routes = _ROUTES.get(provider)
    if routes is None:
        raise ValueError(f"model_returned_provider_not_allowed:{provider}")
    for models in routes.values():
        if model in models:
            return
    raise ValueError(f"model_returned_not_allowed:{provider}:{model}")
