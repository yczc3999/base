"""AI 请求/响应脱敏与 taint 检查（WP-02 Checkpoint B）。

- ``redact_for_storage``：递归移除 Authorization/API key/Cookie/secret 字段并遮挡疑似
  secret echo；任何疑似 secret echo 的原始响应进入 quarantine，不能 ACCEPTED。
- ``detect_taint``：Blind 上下文里出现 quote/odds/crowd/label/future-fact 字段 → 污染。
"""

from __future__ import annotations

import re
from typing import Any

_SENSITIVE_KEY_RE = re.compile(
    r"(?i)(authorization|api[-_]?key|cookie|set-cookie|password|passphrase|"
    r"private[-_]?key|secret|token|signature)"
)
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
                if _SENSITIVE_KEY_RE.search(str(key))
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
    """递归查找 Blind 禁止字段（quote/odds/crowd/label/future-fact）；返回命中路径。"""
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

    walk(value, "")
    return sorted(set(hits))
