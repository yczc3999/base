"""AI 请求/响应脱敏与 taint 检查（WP-02 Checkpoint B）。

- ``redact_for_storage``：递归移除 Authorization/API key/Cookie/secret 字段并遮挡疑似
  secret echo；任何疑似 secret echo 的原始响应进入 quarantine，不能 ACCEPTED。
- ``detect_taint``：Blind 上下文里出现 quote/odds/crowd/label/future-fact 字段 → 污染。
"""

from __future__ import annotations

import json
import re
from typing import Any

_BEARER_RE = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{8,}")
_PRIVATE_KEY_RE = re.compile(r"(?i)-----begin [a-z ]*private key-----")
_URL_USERINFO_RE = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/@:]+:[^\s/@]+@")

# Blind 上下文禁止字段（架构 §2.1/任务 §2.1）
TAINT_KEYS = frozenset(
    {
        "quote", "odds", "price", "bid", "ask", "depth", "market", "crowd",
        "label", "future_fact", "edge", "best_bid", "best_ask",
    }
)

_SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "proxy_authorization",
        "api_key",
        "apikey",
        "cookie",
        "set_cookie",
        "password",
        "passphrase",
        "private_key",
        "secret",
        "secret_key",
        "signature",
        "request_signature",
        "webhook_signature",
        "token",
        "access_token",
        "refresh_token",
        "session_token",
        "auth_token",
        "bearer_token",
        "id_token",
        "csrf_token",
        "oauth_token",
    }
)
_PUBLIC_TOKEN_KEYS = frozenset(
    {
        "token_id",
        "token_ids",
        "clob_token_id",
        "clob_token_ids",
        "input_tokens",
        "cache_tokens",
        "output_tokens",
        "reasoning_tokens",
        "max_tokens",
    }
)


def _is_sensitive_key(key: Any) -> bool:
    """Match credential field boundaries without corrupting public token metadata."""
    normalized = re.sub(r"[-\s]+", "_", str(key).strip().casefold())
    if normalized in _PUBLIC_TOKEN_KEYS:
        return False
    if normalized in _SENSITIVE_KEYS:
        return True
    return normalized.endswith(
        ("_api_key", "_password", "_passphrase", "_private_key", "_secret")
    )


def _secret_echo_in(value: Any) -> bool:
    """值中是否含疑似 secret echo（仅检查字符串值，不检查字段名）。"""
    if isinstance(value, dict):
        return any(_secret_echo_in(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_secret_echo_in(item) for item in value)
    if isinstance(value, str):
        return bool(
            _BEARER_RE.search(value)
            or _PRIVATE_KEY_RE.search(value)
            or _URL_USERINFO_RE.search(value)
        )
    return False


def redact_for_storage(value: Any) -> Any:
    """返回安全副本：敏感 key → [REDACTED]；疑似 secret echo 值 → 整段遮挡。"""
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if _is_sensitive_key(key)
                else redact_for_storage(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_for_storage(item) for item in value]
    if isinstance(value, str):
        if _secret_echo_in(value):
            return "[REDACTED-SECRET-ECHO]"
        return value
    return value


def requires_quarantine(value: Any) -> bool:
    """原始响应/parsed 疑似包含 secret echo → 必须 quarantine，不能 ACCEPTED。"""
    return _secret_echo_in(value)


def detect_taint(value: Any) -> list[str]:
    """递归查找 Blind 禁止字段；JSON 字符串也按其结构递归检查。

    Provider driver 的 ``raw_text`` 是字符串。只检查 ``dict`` 会让
    ``{"nested":{"quote":"0.7"}}`` 绕过 blind 边界，因此对象/数组形态的 JSON
    字符串必须先解码，再沿用同一 key allowlist。普通自然语言字符串不做关键词猜测，
    避免把新闻正文里的单词误当成结构字段。
    """
    hits: list[str] = []

    def walk(item: Any, path: str) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                child_path = f"{path}.{key}" if path else str(key)
                if str(key).casefold() in TAINT_KEYS:
                    hits.append(child_path)
                walk(child, child_path)
        elif isinstance(item, (list, tuple)):
            for index, child in enumerate(item):
                walk(child, f"{path}[{index}]" if path else str(index))
        elif isinstance(item, str):
            candidate = item.strip()
            if not candidate or candidate[0] not in "[{":
                return
            try:
                decoded = json.loads(candidate)
            except (json.JSONDecodeError, TypeError, ValueError):
                return
            if isinstance(decoded, (dict, list)):
                walk(decoded, f"{path}.$json" if path else "$json")

    walk(value, "")
    return sorted(set(hits))
