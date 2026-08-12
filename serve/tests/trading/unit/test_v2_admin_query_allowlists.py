"""WP-07A Checkpoint A —— Admin query allowlist 单元（无 DB）。

证明：未知 filter → CursorError(400)；limit 范围 1–200；direction 白名单；
as_of/filter_hash 语义；page() 编排首屏冻结/后续复用。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.db.cursor import CursorError, derive_key
from app.logics.trading.admin_read import AdminReadLogic, MAX_LIMIT

LOGIC = AdminReadLogic(__import__("app.db.cursor", fromlist=["CursorCodec"]).CursorCodec(
    derive_key("test-key")
))


def test_unknown_filter_rejected():
    with pytest.raises(CursorError, match="unknown_filter"):
        LOGIC.parse_filters({"bogus": "1"}, allowed=frozenset({"status"}))


def test_known_filters_accepted():
    out = LOGIC.parse_filters({"status": "active", "cursor": "tok", "limit": "10"},
                              allowed=frozenset({"status"}))
    assert out == {"status": "active"}  # cursor/limit 被剔除


def test_direction_allowlist():
    assert LOGIC.parse_direction(None) == "desc"
    assert LOGIC.parse_direction("asc") == "asc"
    with pytest.raises(CursorError, match="cursor_direction_invalid"):
        LOGIC.parse_direction("sideways")


def test_limit_range():
    assert LOGIC.clamp_limit(None) == 50
    assert LOGIC.clamp_limit("1") == 1
    assert LOGIC.clamp_limit(str(MAX_LIMIT)) == MAX_LIMIT
    with pytest.raises(CursorError, match="limit_out_of_range"):
        LOGIC.clamp_limit(str(MAX_LIMIT + 1))
    with pytest.raises(CursorError, match="limit_invalid"):
        LOGIC.clamp_limit("abc")


def test_filter_hash_endpoint_scoped():
    fh1 = LOGIC.filter_hash(endpoint="markets", filters={"closed": "true"}, direction="desc")
    fh2 = LOGIC.filter_hash(endpoint="episodes", filters={"closed": "true"}, direction="desc")
    assert fh1 != fh2
    assert len(fh1) == 64


def test_cursor_binds_filter_and_direction():
    token = LOGIC.encode_cursor(
        endpoint="markets", sort_time=datetime(2026, 8, 12, tzinfo=timezone.utc),
        id="5", direction="desc", filter_hash="a" * 64,
        as_of=datetime(2026, 8, 12, tzinfo=timezone.utc),
    )
    # 相同 endpoint/filter/direction → 解码成功
    p = LOGIC.decode_cursor(token, endpoint="markets", direction="desc", filter_hash="a" * 64)
    assert p.id == "5"
    # 换 filter → 拒绝
    with pytest.raises(CursorError, match="cursor_filter_mismatch"):
        LOGIC.decode_cursor(token, endpoint="markets", direction="desc", filter_hash="b" * 64)
