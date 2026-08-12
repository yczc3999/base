"""WP-07A Checkpoint A —— Admin read DTO 单元（无 DB）。

证明：BIGINT/NUMERIC 全为 string；CursorPage envelope 严格；authoritative/投影块标记。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.trading.admin import (
    ArtifactContentResponse,
    ArtifactMetadata,
    Authoritative,
    CursorPage,
    DashboardResponse,
    ProjectionBlock,
)


def test_cursor_page_accepts_string_ids():
    page = CursorPage[str](
        items=["a", "b"],
        next_cursor="tok",
        has_more=True,
        as_of="2026-08-12T00:00:00Z",
        filter_hash="a" * 64,
    )
    assert page.items == ["a", "b"]
    assert page.next_cursor == "tok"
    assert page.has_more is True


def test_cursor_page_rejects_bad_filter_hash():
    with pytest.raises(ValidationError):
        CursorPage[dict](
            items=[], next_cursor=None, has_more=False,
            as_of="2026-08-12T00:00:00Z", filter_hash="short",
        )


def test_cursor_page_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        CursorPage[dict](
            items=[], next_cursor=None, has_more=False,
            as_of="2026-08-12T00:00:00Z", filter_hash="a" * 64, total=5,
        )


def test_authoritative_marker():
    auth = Authoritative(authoritative=True, as_of="2026-08-12T00:00:00Z")
    assert auth.authoritative is True


def test_projection_block_requires_hash_and_status():
    with pytest.raises(ValidationError):
        ProjectionBlock(
            as_of="2026-08-12T00:00:00Z", projection_version=1,
            projection_hash="bad", freshness_status="unknown",
        )
    block = ProjectionBlock(
        as_of="2026-08-12T00:00:00Z", projection_version=1,
        projection_hash="b" * 64, freshness_status="fresh",
    )
    assert block.freshness_status == "fresh"


def test_artifact_metadata_no_storage_path_leak():
    meta = ArtifactMetadata(
        content_hash="a" * 64, content_type="application/json",
        content_length=10, lineage=[], stored_at="2026-08-12T00:00:00Z",
    )
    # 无 locator/path/bucket credential 字段
    assert "locator" not in meta.model_dump()
    assert "storage_driver" not in meta.model_dump()


def test_artifact_content_response_range_fields():
    resp = ArtifactContentResponse(
        content_hash="a" * 64, content_type="application/json",
        start=0, end=99, total=100, etag='"a" * 64',
    )
    assert resp.end >= resp.start
