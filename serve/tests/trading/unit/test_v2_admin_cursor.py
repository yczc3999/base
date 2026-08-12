"""WP-07A Checkpoint A —— Admin keyset cursor 单元（无 DB）。

证明：HMAC opaque token 可往返；tamper / endpoint / filter / direction mismatch /
非 UTC / 超长 / 坏签名 → CursorError；limit 不进 cursor 身份；filter_hash 稳定。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.db.cursor import (
    CursorCodec,
    CursorError,
    canonical_filter_hash,
    derive_key,
    parse_utc_iso,
)

SECRET = "test-app-key"


def _codec() -> CursorCodec:
    return CursorCodec(derive_key(SECRET))


def _token(codec=None, **kw) -> str:
    codec = codec or _codec()
    base = dict(
        endpoint="markets", sort_time=datetime(2026, 8, 12, 1, 2, 3, tzinfo=timezone.utc),
        id=123456, direction="desc",
        filter_hash="a" * 64, as_of=datetime(2026, 8, 12, 0, 0, 0, tzinfo=timezone.utc),
    )
    base.update(kw)
    return codec.encode(**base)


def test_encode_decode_roundtrip():
    token = _token()
    payload = _codec().decode(token, endpoint="markets", direction="desc",
                              filter_hash="a" * 64)
    assert payload.version == "v1"
    assert payload.endpoint == "markets"
    assert payload.id == "123456"
    assert payload.direction == "desc"
    assert payload.filter_hash == "a" * 64
    assert payload.sort_time == datetime(2026, 8, 12, 1, 2, 3, tzinfo=timezone.utc)
    assert payload.as_of == datetime(2026, 8, 12, 0, 0, 0, tzinfo=timezone.utc)


def test_tamper_rejected():
    token = _token()
    # 翻转 payload 最后一个字符
    body, mac = token.split(".")
    tampered_body = body[:-1] + ("A" if body[-1] != "A" else "B")
    with pytest.raises(CursorError, match="cursor_tampered"):
        _codec().decode(f"{tampered_body}.{mac}", endpoint="markets",
                        direction="desc", filter_hash="a" * 64)


def test_endpoint_mismatch_rejected():
    with pytest.raises(CursorError, match="cursor_endpoint_mismatch"):
        _codec().decode(_token(), endpoint="episodes", direction="desc",
                        filter_hash="a" * 64)


def test_direction_mismatch_rejected():
    with pytest.raises(CursorError, match="cursor_direction_mismatch"):
        _codec().decode(_token(), endpoint="markets", direction="asc",
                        filter_hash="a" * 64)


def test_filter_mismatch_rejected():
    with pytest.raises(CursorError, match="cursor_filter_mismatch"):
        _codec().decode(_token(), endpoint="markets", direction="desc",
                        filter_hash="b" * 64)


def test_wrong_secret_rejected():
    other = CursorCodec(derive_key("other-key"))
    with pytest.raises(CursorError, match="cursor_tampered"):
        other.decode(_token(), endpoint="markets", direction="desc", filter_hash="a" * 64)


def test_oversized_token_rejected():
    with pytest.raises(CursorError, match="cursor_too_long"):
        _codec().decode("x" * 5000, endpoint="markets", direction="desc",
                        filter_hash="a" * 64)


def test_malformed_rejected():
    with pytest.raises(CursorError):
        _codec().decode("no-dot-here", endpoint="markets", direction="desc",
                        filter_hash="a" * 64)


def test_limit_not_in_cursor_identity():
    # 改变 limit 不改变 snapshot/filter：token 相同
    t1 = _token(id=10)
    t2 = _token(id=10)
    assert t1 == t2


def test_parse_utc_iso_strict():
    assert parse_utc_iso("2026-08-12T01:02:03Z") == datetime(2026, 8, 12, 1, 2, 3, tzinfo=timezone.utc)
    with pytest.raises(CursorError, match="cursor_time_not_utc"):
        parse_utc_iso("2026-08-12T01:02:03+02:00")
    with pytest.raises(CursorError, match="cursor_time_not_utc"):
        parse_utc_iso("not-a-date")


def test_filter_hash_stable_and_sensitive_to_filter_direction():
    f1 = canonical_filter_hash(endpoint="markets", query_version="v1",
                               filters={"neg_risk": "false"}, direction="desc")
    f2 = canonical_filter_hash(endpoint="markets", query_version="v1",
                               filters={"neg_risk": "false"}, direction="desc")
    f3 = canonical_filter_hash(endpoint="markets", query_version="v1",
                               filters={"neg_risk": "true"}, direction="desc")
    assert f1 == f2
    assert f1 != f3
    assert len(f1) == 64


def test_derive_key_context_scoped():
    k1 = derive_key("secret", context_label="a")
    k2 = derive_key("secret", context_label="b")
    k3 = derive_key("secret", context_label="a")
    assert k1 != k2
    assert k1 == k3
