"""Tag catalog：处置校验 + 同步只写有官方 id 的 tag。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.logics.trading.tag_catalog import TagCatalogLogic
from app.schemas.polymarket.gamma import GammaTag


class _Market:
    def __init__(self) -> None:
        self.updated: list[tuple[int, str | None]] = []
        self.missing = False

    async def update_tag_disposition(self, session, *, tag_id, disposition):
        if self.missing:
            return None
        self.updated.append((tag_id, disposition))
        return {"id": str(tag_id), "disposition": disposition}


class _Universe:
    def __init__(self) -> None:
        self.policy = SimpleNamespace(tag_page_limit=2, tag_catalog_max_pages=10)
        self.persisted: list[str] = []

    async def persist_tag(self, uow, tag, *, observed_at, seen_in_catalog=False, seen_in_event=False):
        if not tag.persistable():
            return None
        self.persisted.append(tag.id)
        return 1


class _Session:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


class _Gamma:
    def __init__(self, pages: list[list[GammaTag]]) -> None:
        self.pages = pages

    async def list_tags(self, *, limit: int, offset: int):
        index = offset // limit
        items = self.pages[index] if index < len(self.pages) else []
        return SimpleNamespace(typed=SimpleNamespace(items=items))


def _run(coro):
    return asyncio.run(coro)


def test_set_disposition_rejects_bad_id():
    logic = TagCatalogLogic(market_repo=_Market(), universe=_Universe())
    with pytest.raises(ValueError, match="tag_id_invalid"):
        _run(logic.set_disposition(None, tag_id=0, disposition="SELECT"))
    with pytest.raises(ValueError, match="tag_id_invalid"):
        _run(logic.set_disposition(None, tag_id=True, disposition="SELECT"))


def test_set_disposition_rejects_unknown():
    logic = TagCatalogLogic(market_repo=_Market(), universe=_Universe())
    with pytest.raises(ValueError, match="tag_disposition_invalid"):
        _run(logic.set_disposition(None, tag_id=1, disposition="MAYBE"))


def test_set_disposition_not_found():
    market = _Market()
    market.missing = True
    logic = TagCatalogLogic(market_repo=market, universe=_Universe())
    with pytest.raises(ValueError, match="tag_not_found"):
        _run(logic.set_disposition(None, tag_id=9, disposition="SELECT"))


def test_set_disposition_accepts_clear():
    market = _Market()
    logic = TagCatalogLogic(market_repo=market, universe=_Universe())
    row = _run(logic.set_disposition(None, tag_id=3, disposition=None))
    assert market.updated == [(3, None)]
    assert row["disposition"] is None


def test_sync_skips_tags_without_official_id():
    universe = _Universe()
    logic = TagCatalogLogic(market_repo=_Market(), universe=universe)
    gamma = _Gamma(
        [
            [
                GammaTag(id="2", slug="politics", label="Politics"),
                GammaTag(id="", slug="guessed", label="guessed"),
            ]
        ]
    )
    session = _Session()
    result = _run(
        logic.sync_catalog(
            session,
            gamma=gamma,
            observed_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
        )
    )
    assert result["ok"] is True
    assert result["upserted"] == 1
    assert universe.persisted == ["2"]
    assert session.commits == 1


def test_sync_commits_each_page():
    universe = _Universe()
    logic = TagCatalogLogic(market_repo=_Market(), universe=universe)
    gamma = _Gamma(
        [
            [GammaTag(id="2", slug="politics"), GammaTag(id="21", slug="crypto")],
            [GammaTag(id="1", slug="sports")],
        ]
    )
    session = _Session()
    result = _run(logic.sync_catalog(session, gamma=gamma))
    assert result["pages"] == 2
    assert result["upserted"] == 3
    assert session.commits == 2
