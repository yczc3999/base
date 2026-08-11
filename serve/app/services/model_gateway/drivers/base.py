"""Driver 公共框架：wire mapping + 脱敏（WP-02 Checkpoint B）。

Driver 只做 wire mapping；HTTP 调用通过注入的 ``transport``（离线测试用 fake transport，
生产用真实 HTTP 客户端）。请求/响应正文只进 Artifact Store，Driver 不落库、不做业务判断。
"""

from __future__ import annotations

import re
from typing import Any, Callable

from app.services.model_gateway.contracts import (
    ModelRequest,
    ModelResponse,
    NETWORK_NONE,
    ProviderError,
)

# 递归移除敏感 header/key 的正则集（任务 §5.3）
_SENSITIVE_KEY_RE = re.compile(
    r"(?i)(authorization|api[-_]?key|cookie|set-cookie|password|passphrase|"
    r"private[-_]?key|secret|token|signature)"
)

# 疑似 secret echo 模式：Bearer/AWS SigV4/私有 key
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{8,}")
_SIGV4_RE = re.compile(r"(?i)aws4[-_]hmac")
_PRIVATE_KEY_RE = re.compile(r"-----begin [a-z ]*private key-----")


def redact(value: Any) -> Any:
    """递归移除/遮挡 secret 字段与疑似 secret echo；返回安全副本。"""
    if isinstance(value, dict):
        return {
            key: ("[REDACTED]" if _SENSITIVE_KEY_RE.search(str(key)) else redact(item))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_text(text: str) -> str:
    if _BEARER_RE.search(text) or _SIGV4_RE.search(text) or _PRIVATE_KEY_RE.search(text):
        return "[REDACTED-SECRET-ECHO]"
    return text


def looks_like_secret_echo(value: Any) -> bool:
    """疑似 secret echo → 返回 True；Runner 据此 quarantine，不能 ACCEPTED。"""
    if isinstance(value, dict):
        return any(_SENSITIVE_KEY_RE.search(str(key)) or looks_like_secret_echo(item)
                   for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(looks_like_secret_echo(item) for item in value)
    if isinstance(value, str):
        return bool(
            _BEARER_RE.search(value) or _SIGV4_RE.search(value) or _PRIVATE_KEY_RE.search(value)
        )
    return False


class ModelDriver:
    """Driver 基类：wire mapping + 脱敏 + transport 注入。"""

    driver_name: str
    provider: str

    def __init__(self, transport: Callable[..., Any], *, timeout: float = 60.0) -> None:
        self._transport = transport
        self._timeout = timeout

    async def request(self, model_request: ModelRequest) -> ModelResponse:
        """执行一次 provider request，返回规范化 ModelResponse。

        子类负责：组装 wire payload（已脱敏）→ 调用 transport → 解析/规范化。
        任何失败以 ProviderError 结构化抛出（不含 secret/raw body）。
        """
        raise NotImplementedError

    def _assert_no_network_for_none(self, model_request: ModelRequest) -> None:
        if model_request.network_policy == NETWORK_NONE:
            if model_request.allowed_tools or model_request.allowed_domains:
                raise ProviderError("network_none_with_tools", retriable=False)
