"""Canonical hashing 与确定性审计抽样（WP-01C Checkpoint A/B）。

- ``canonical_bytes/hash``：与 outbox canonical 同源（sort_keys + separators + ensure_ascii），
  Decimal 用 ``normalize()+f`` 规范化（消除 NUMERIC scale 尾零），保证跨存储 scale 稳定。
- ``deterministic_sample``：基于 HMAC-SHA256(content||seed||stratum||salt) 的确定性抽样；
  输入顺序变化或重试不得改变结果（任务 §2.8）。返回 (selected, u, inclusion_probability)。
"""

from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal, localcontext
from typing import Any

_CANONICAL_KW = {"sort_keys": True, "separators": (",", ":"), "ensure_ascii": False}


def _decimal_canonical(value: Decimal) -> str:
    return format(value.normalize(), "f")


def canonical_bytes(content: Any) -> bytes:
    """规范 JSON 序列化；Decimal 按规范化文本，datetime 转 UTC ISO，其余按 json.dumps。"""
    def _fold(value: Any) -> Any:
        if isinstance(value, Decimal):
            return _decimal_canonical(value)
        if isinstance(value, dict):
            return {str(k): _fold(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_fold(v) for v in value]
        if hasattr(value, "isoformat"):
            from datetime import datetime, timezone

            dt = value
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat()
        return value

    return json.dumps(_fold(content), **_CANONICAL_KW).encode("utf-8")


def canonical_hash(content: Any) -> str:
    return hashlib.sha256(canonical_bytes(content)).hexdigest()


def deterministic_sample(
    *,
    content_hash: str,
    seed_hash: str,
    stratum: str,
    rate: Decimal | str | int | float,
    salt: str = "reject-audit/v1",
) -> tuple[bool, Decimal, Decimal]:
    """确定性抽样：HMAC-SHA256 截取为 [0,1) 均匀值，低于 rate 即选中。

    同 (content_hash, seed_hash, stratum, salt) 永远返回相同 (selected, u, rate)。
    ``u`` 是均匀样本（供 inclusion probability/加权），``rate`` 为 0≤rate≤1 的包含概率。
    """
    if isinstance(rate, bool):
        raise ValueError(f"rate must be in [0,1], got {rate!r}")
    try:
        probability = Decimal(str(rate))
    except Exception as exc:
        raise ValueError(f"rate must be in [0,1], got {rate!r}") from exc
    if not probability.is_finite() or not (Decimal(0) <= probability <= Decimal(1)):
        raise ValueError(f"rate must be in [0,1], got {rate!r}")
    if not _is_sha256(content_hash):
        raise ValueError("content_hash must be 64 hex")
    if not _is_sha256(seed_hash):
        raise ValueError("seed_hash must be 64 hex")
    digest = hmac.new(
        bytes.fromhex(seed_hash),
        f"{content_hash}\x1f{stratum}\x1f{salt}".encode("utf-8"),
        hashlib.sha256,
    ).digest()
    # 取前 8 字节 → 0..2^64-1；全程 Decimal，不在 float 中丢掉 11 bit。
    with localcontext() as ctx:
        ctx.prec = 80
        u = Decimal(int.from_bytes(digest[:8], "big")) / Decimal(1 << 64)
    return u < probability, u, probability


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(char in "0123456789abcdef" for char in value)
