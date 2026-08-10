"""Polymarket 公共行情 WP-01B 共享测试 fixture。

fixture 来自文档列明的官方格式（docs.polymarket.com discover-markets/prices-order-books/
api-reference/wss/market），已去除凭据；不做“美化”。测试不得访问公网。本模块也提供
真 PostgreSQL 测试共用的已发布 release chain，避免各测试伪造悬空 FK。
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

FIXTURE_DIR = (
    Path(__file__).resolve().parents[1] / "contract" / "fixtures" / "polymarket_public"
)


def fixture_path(name: str) -> Path:
    path = FIXTURE_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"missing polymarket fixture: {path}")
    return path


def load_fixture(name: str) -> dict:
    return json.loads(fixture_path(name).read_text(encoding="utf-8"))


def load_raw_fixture(name: str) -> bytes:
    return fixture_path(name).read_bytes()


def fixture_names() -> list[str]:
    return sorted(p.name for p in FIXTURE_DIR.glob("*.json"))


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def create_test_release_manifest(
    session: AsyncSession,
    *,
    key: str = "wp01b-public-market-data",
) -> int:
    """Create and publish a complete test release chain in ``session``.

    The caller owns the transaction.  Every object is inserted as ``draft`` and then
    published as ``active``, so the database's published-control guards make its payload
    immutable.  Repeated calls with the same ``key`` reuse the matching active release.
    """
    # Keep production model imports lazy: contract-only tests using the JSON loaders must
    # not register ORM metadata as an incidental side effect.
    from app.models.trading.control import (
        CapitalPermissionManifest,
        ExecutionSpecVersion,
        ReleaseManifest,
        RuntimeConfigVersion,
        StrategyVersion,
    )

    config_content = {
        "environment": "test",
        "mode": "shadow",
        "provider": "polymarket",
    }
    strategy_content = {
        "name": "public-market-data-fixture",
        "roles": [],
    }
    execution_content = {
        "execution_enabled": False,
        "mode": "shadow",
    }
    capability = {"trade": False}
    limits = {"max_notional_base_units": 0}

    config_hash = _canonical_hash(config_content)
    strategy_hash = _canonical_hash(strategy_content)
    execution_hash = _canonical_hash(execution_content)
    capital_hash = _canonical_hash(
        {
            "authorized_capital": 0,
            "capability": capability,
            "evaluation_capital": 0,
            "kill_switch": True,
            "limits": limits,
            "mode": "shadow",
        }
    )
    git_sha = hashlib.sha256(b"wp01b-test-git").hexdigest()
    image_digest = f"sha256:{hashlib.sha256(b'wp01b-test-image').hexdigest()}"
    db_revision = "b1000011"
    total_hash = _canonical_hash(
        {
            "capital": capital_hash,
            "config": config_hash,
            "db_revision": db_revision,
            "execution": execution_hash,
            "git_sha": git_sha,
            "image_digest": image_digest,
            "strategy": strategy_hash,
        }
    )
    release_name = f"{key}-release"

    existing = await session.scalar(
        select(ReleaseManifest.id)
        .where(
            ReleaseManifest.release_name == release_name,
            ReleaseManifest.total_hash == total_hash,
            ReleaseManifest.status == "active",
        )
        .order_by(ReleaseManifest.id)
        .limit(1)
    )
    if existing is not None:
        return int(existing)

    config = RuntimeConfigVersion(
        config_key=f"{key}-config",
        version_no=1,
        content=config_content,
        schema_version=1,
        content_hash=config_hash,
        status="draft",
        creator="test-fixture",
    )
    strategy = StrategyVersion(
        strategy_key=f"{key}-strategy",
        version_no=1,
        content=strategy_content,
        schema_version=1,
        content_hash=strategy_hash,
        status="draft",
        creator="test-fixture",
    )
    execution = ExecutionSpecVersion(
        spec_key=f"{key}-execution",
        version_no=1,
        content=execution_content,
        schema_version=1,
        content_hash=execution_hash,
        status="draft",
        creator="test-fixture",
    )
    capital = CapitalPermissionManifest(
        name=f"{key}-capital",
        mode="shadow",
        capability=capability,
        limits=limits,
        evaluation_capital=Decimal(0),
        authorized_capital=Decimal(0),
        kill_switch=True,
        content_hash=capital_hash,
        status="draft",
        creator="test-fixture",
    )
    session.add_all((config, strategy, execution, capital))
    await session.flush()

    release = ReleaseManifest(
        release_name=release_name,
        config_version_id=config.id,
        strategy_version_id=strategy.id,
        execution_spec_version_id=execution.id,
        capital_permission_manifest_id=capital.id,
        git_sha=git_sha,
        image_digest=image_digest,
        db_revision=db_revision,
        total_hash=total_hash,
        status="draft",
        creator="test-fixture",
    )
    session.add(release)
    await session.flush()

    for item in (config, strategy, execution, capital, release):
        item.status = "active"
    await session.flush()
    return int(release.id)
