"""Model gateway：provider-neutral 调用底座（WP-02 Checkpoint B）。

contracts（类型/错误）→ registry（allowlist）→ drivers（wire mapping）→ service（冻结 binding）。
"""

from app.services.model_gateway.contracts import (
    NETWORK_NONE,
    NETWORK_SEARCH_URL,
    NETWORK_WEB_X,
    ModelRequest,
    ModelResponse,
    ProviderError,
    ToolReceipt,
)
from app.services.model_gateway.drivers import (
    DRIVER_BY_PROVIDER,
    DeepSeekDriver,
    GeminiDriver,
    KimiDriver,
    ModelDriver,
    PackyDriver,
    XAIDriver,
    looks_like_secret_echo,
    redact,
)
from app.services.model_gateway.registry import (
    RouteModel,
    assert_returned_model,
    resolve,
)
from app.services.model_gateway.service import ModelGatewayService

__all__ = [
    "NETWORK_NONE",
    "NETWORK_WEB_X",
    "NETWORK_SEARCH_URL",
    "ModelRequest",
    "ModelResponse",
    "ProviderError",
    "ToolReceipt",
    "ModelDriver",
    "DeepSeekDriver",
    "XAIDriver",
    "GeminiDriver",
    "KimiDriver",
    "PackyDriver",
    "DRIVER_BY_PROVIDER",
    "looks_like_secret_echo",
    "redact",
    "RouteModel",
    "resolve",
    "assert_returned_model",
    "ModelGatewayService",
]
