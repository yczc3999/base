"""生产 HTTP transport factory（WP-07C Checkpoint B）。

把冻结 model_role_binding 的 provider 变成真实 ``httpx`` 调用。这是
``ModelGatewayService(transport_factory)`` 的生产实现；此前网关只在测试里用 fake
transport，本模块首次接通真实 provider。

边界（v2-implementation-contract §5 / model-selection.md）：
- 凭证只从**环境变量**读（``PM_V2_<PROVIDER>_API_KEY``），不落库/日志/fixture/版本库；
  ``Authorization: Bearer`` 在 transport 内注入，driver 只发业务 header。
- base_url 有官方默认值，可被 config 字段覆盖（Packy relay 用 ``PM_V2_PACKY_BASE_URL``）。
- provider allowlist 由 ``registry._ROUTES`` 控制；本 factory 不新增 provider，
  gemini 不注册（verifier 走代码规则，见 model-selection §5）。
- 任何网络失败由 driver 包装为结构化 ``ProviderError``（不含 secret/raw body）。
"""

from __future__ import annotations

import os
from typing import Any, Callable

import httpx

from app.services.model_gateway.contracts import ProviderError

# provider → (官方默认 base_url, 凭证环境变量名)
# base_url 可被 config 覆盖；env 键固定。
_PROVIDER_DEFAULTS: dict[str, tuple[str, str]] = {
    "deepseek": ("https://api.deepseek.com/v1", "PM_V2_DEEPSEEK_API_KEY"),
    "xai": ("https://api.x.ai/v1", "PM_V2_XAI_API_KEY"),
    "kimi": ("https://api.moonshot.cn/v1", "PM_V2_KIMI_API_KEY"),
    # Packy relay（deepseek-officially / kimi-sale / grok-sale 兜底）共用此 base。
    "packy": ("https://www.packyapi.ai/v1", "PM_V2_PACKY_API_KEY"),
}


def _read_secret(env_key: str) -> str:
    """从环境读凭证；缺失/空白 → fail closed（不返回空 key 让请求带着无效凭证出网）。"""
    value = os.environ.get(env_key, "").strip()
    if not value:
        raise ProviderError(
            f"model_credential_missing:{env_key}", retriable=False
        )
    return value


def _join(base_url: str, endpoint: str) -> str:
    """拼接 base_url 与 endpoint，去除重复的版本前缀。

    driver 的 endpoint 自包含路径（xai/packy 用 ``/v1/chat/completions``，
    deepseek/kimi 用 ``/chat/completions``），而 base_url 含 ``/v1`` 版本前缀。
    两者同时带 ``/v1`` 时会拼出 ``/v1/v1/``。这里在端点以 base 尾路径开头时去重，
    不改冻结的 driver、也不猜真实 base 是否含版本段。
    """
    base = base_url.rstrip("/")
    if not endpoint.startswith("/"):
        endpoint = "/" + endpoint
    # base 尾段（如 "/v1"）若恰是 endpoint 的前缀，去重一次。
    if "/" in base:
        tail = base[base.rindex("/"):]
        if tail and endpoint.startswith(tail + "/"):
            endpoint = endpoint[len(tail):]
    return base + endpoint


def build_transport_factory(
    *,
    base_url_overrides: dict[str, str] | None = None,
    default_timeout: float = 60.0,
) -> Callable[[str], Callable[..., Any]]:
    """返回 ``transport_factory(provider) -> async transport``。

    transport 签名对齐 driver 调用：
    ``async transport(endpoint=..., headers=..., json=..., timeout=...) -> (status, body)``
    """
    overrides = base_url_overrides or {}

    def factory(provider: str) -> Callable[..., Any]:
        if provider not in _PROVIDER_DEFAULTS:
            raise ProviderError(f"model_provider_no_transport:{provider}", retriable=False)
        default_base, env_key = _PROVIDER_DEFAULTS[provider]
        base_url = (overrides.get(provider) or default_base).rstrip("/")
        api_key = _read_secret(env_key)

        async def transport(
            *,
            endpoint: str,
            headers: dict[str, str] | None = None,
            json: Any = None,
            timeout: float | None = None,
        ) -> tuple[int, Any]:
            url = _join(base_url, endpoint)
            merged = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            # driver 传入的 header 不含凭证；合并但不允许覆盖 Authorization。
            for key, value in (headers or {}).items():
                if key.lower() != "authorization":
                    merged[key] = value
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(timeout or default_timeout)
                ) as client:
                    response = await client.post(url, headers=merged, json=json)
            except httpx.TimeoutException as exc:
                raise ProviderError(f"{provider}_timeout", retriable=True) from exc
            except httpx.HTTPError as exc:
                raise ProviderError(f"{provider}_http_error", retriable=True) from exc
            return response.status_code, response.text

        return transport

    return factory
