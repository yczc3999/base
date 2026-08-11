"""Model gateway drivers：provider wire mapping（WP-02 Checkpoint B）。"""

from app.services.model_gateway.drivers.base import (
    ModelDriver,
    looks_like_secret_echo,
    redact,
)
from app.services.model_gateway.drivers.deepseek import DeepSeekDriver
from app.services.model_gateway.drivers.gemini import GeminiDriver
from app.services.model_gateway.drivers.kimi import KimiDriver
from app.services.model_gateway.drivers.packy import PackyDriver
from app.services.model_gateway.drivers.xai import XAIDriver

DRIVER_BY_PROVIDER = {
    "deepseek": DeepSeekDriver,
    "xai": XAIDriver,
    "gemini": GeminiDriver,
    "kimi": KimiDriver,
    "packy": PackyDriver,
}

__all__ = [
    "ModelDriver",
    "DeepSeekDriver",
    "XAIDriver",
    "GeminiDriver",
    "KimiDriver",
    "PackyDriver",
    "DRIVER_BY_PROVIDER",
    "looks_like_secret_echo",
    "redact",
]
