"""Gamma tag 解析与 Driver：官方对象入库，字符串 fixture 不猜 id。"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.schemas.polymarket.common import PolymarketError, REASON_RESPONSE_SCHEMA
from app.schemas.polymarket.gamma import (
    GAMMA_TAGS_PAGE_LIMIT,
    GammaEvent,
    GammaTag,
    parse_gamma_tags_page,
)
from app.services.polymarket.base import WirePolicy
from app.services.polymarket.gamma_driver import GammaDriver


def _driver(handler):
    policy = WirePolicy(
        connect_timeout_s=0.5,
        read_timeout_s=0.5,
        max_retries=1,
        base_backoff_s=0.01,
        max_backoff_s=0.02,
        jitter_s=0.0,
        rate_per_second=1000,
        rate_burst=1000,
    )
    return GammaDriver(
        "https://gamma.example",
        policy=policy,
        transport=httpx.MockTransport(handler),
    )


def test_tag_object_is_persistable():
    tag = GammaTag.model_validate(
        {"id": "2", "slug": "politics", "label": "Politics"}
    )
    assert tag.id == "2"
    assert tag.slug == "politics"
    assert tag.persistable() is True


def test_tag_numeric_id_coerced_to_str():
    tag = GammaTag.model_validate({"id": 21, "slug": "crypto"})
    assert tag.id == "21"
    assert tag.persistable() is True


def test_event_string_tags_parse_but_are_not_persistable():
    event = GammaEvent.model_validate(
        {"id": "evt-1", "tags": ["politics", "election"]}
    )
    assert [tag.slug for tag in event.tags] == ["politics", "election"]
    assert all(not tag.persistable() for tag in event.tags)


def test_event_object_tags_keep_official_id():
    event = GammaEvent.model_validate(
        {
            "id": "16183",
            "tags": [
                {"id": "21", "slug": "crypto", "label": "Crypto"},
                {"id": "120", "slug": "finance", "label": "Finance"},
            ],
        }
    )
    assert [tag.id for tag in event.tags] == ["21", "120"]
    assert all(tag.persistable() for tag in event.tags)


def test_tags_page_must_be_array():
    with pytest.raises(ValueError, match="tags_response_not_array"):
        parse_gamma_tags_page({"tags": []})


def test_list_tags_uses_offset_only_on_catalog_path():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, dict(request.url.params)))
        return httpx.Response(
            200,
            json=[{"id": "2", "slug": "politics", "label": "Politics"}],
        )

    result = asyncio.run(_driver(handler).list_tags(limit=20, offset=40))
    assert seen == [("/tags", {"limit": "20", "offset": "40"})]
    assert result.typed.items[0].id == "2"
    assert result.typed.offset == 40


def test_list_tags_rejects_bad_offset_and_limit():
    driver = _driver(lambda req: httpx.Response(200, json=[]))
    with pytest.raises(ValueError):
        asyncio.run(driver.list_tags(limit=0))
    with pytest.raises(ValueError):
        asyncio.run(driver.list_tags(limit=GAMMA_TAGS_PAGE_LIMIT + 1))
    with pytest.raises(ValueError):
        asyncio.run(driver.list_tags(offset=-1))
    with pytest.raises(ValueError):
        asyncio.run(driver.list_tags(offset=True))


def test_tag_by_slug_requires_official_id():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/tags/slug/politics"
        return httpx.Response(200, json={"id": "2", "slug": "politics", "label": "Politics"})

    result = asyncio.run(_driver(handler).tag_by_slug("politics"))
    assert result.typed.id == "2"


def test_tag_by_slug_rejects_path_injection():
    driver = _driver(lambda req: httpx.Response(200, json={"id": "1"}))
    with pytest.raises(ValueError, match="tag_slug_invalid"):
        asyncio.run(driver.tag_by_slug("politics/../x"))


def test_tag_by_slug_empty_id_is_schema_error():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"slug": "culture", "label": "Culture"})

    with pytest.raises(PolymarketError) as exc:
        asyncio.run(_driver(handler).tag_by_slug("culture"))
    assert exc.value.reason_code == REASON_RESPONSE_SCHEMA
