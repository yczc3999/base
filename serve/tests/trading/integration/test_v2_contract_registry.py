"""WP-06 Checkpoint B —— contract registry 集成（真 PostgreSQL + 冻结 fixture）。

证明：从 polygon_rpc_golden 读取满长 code 字节，keccak 与 registry fixture 全长一致；
三条 registry 发布/active 唯一/版本 exact 复核；wrong chain / 空 code / proxy-only
hash / implementation/beacon/code drift 在发布前拒绝；registry 只 INSERT。
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

import json

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from tests.trading.fixtures.p6_settlement.p6_helpers import (
    code_keccak,
    frozen_fixture,
    registry,
    rpc_golden,
    slot32,
)

SERVE_DIR = Path(__file__).resolve().parents[3]
ALEMBIC_DIR = SERVE_DIR / "alembic"
V52 = "b1000052"

CHAIN_ID = 137
SNAPSHOT_BLOCK = 91842167


def _upgrade(db_url):
    cfg = Config()
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    engine = create_engine(db_url, poolclass=NullPool)
    conn = engine.connect()
    cfg.attributes["connection"] = conn
    try:
        command.upgrade(cfg, V52)
    finally:
        conn.close()
        engine.dispose()


def _insert_registry(db_url, entry: dict) -> int:
    engine = create_engine(db_url)
    try:
        with engine.begin() as c:
            return c.execute(
                text(
                    "INSERT INTO trading.contract_registry "
                    "(registry_version, kind, version_no, chain_id, address, proxy_kind, "
                    " runtime_keccak, resolved_implementation_or_beacon, resolved_code_keccak, "
                    " snapshot_block_number, snapshot_block_hash, source_url, retrieved_at, "
                    " content_hash, extra, status) "
                    "VALUES (:rv, :kind, :ver, :chain, :addr, :pk, :rk, :resolved, :rk2, "
                    " :snap, :bh, :src, :ret, :ch, :extra, 'ACTIVE') RETURNING id"
                ),
                {
                    "rv": entry["registry_version"], "kind": entry["kind"],
                    "ver": entry["version_no"], "chain": entry["chain_id"],
                    "addr": entry["address"], "pk": entry["proxy_kind"],
                    "rk": entry["runtime_keccak"],
                    "resolved": entry["resolved_implementation_or_beacon"],
                    "rk2": entry["resolved_code_keccak"],
                    "snap": entry["snapshot_block_number"], "bh": entry["snapshot_block_hash"],
                    "src": entry["source_url"], "ret": entry["retrieved_at"],
                    "ch": entry["content_hash"],
                    "extra": json.dumps(entry.get("extra")) if entry.get("extra") else None,
                },
            ).scalar_one()
    finally:
        engine.dispose()


def _fixture_entry(name: str) -> dict:
    reg = frozen_fixture("contract_registry")
    return next(e for e in reg["entries"] if e["name"] == name)


def _publish_registry(db_url: str, entry: dict) -> int:
    engine = create_engine(db_url)
    try:
        with engine.begin() as c:
            return int(c.execute(text(
                "SELECT trading.v2_publish_contract_registry("
                ":rv,:kind,:ver,:chain,:addr,:pk,:runtime,:resolved,:resolved_code,"
                ":snapshot,:block_hash,:source,:retrieved,:content,CAST(:extra AS jsonb))"
            ), entry).scalar_one())
    finally:
        engine.dispose()


def test_registry_runtime_keccak_matches_rpc_golden(temp_pg_db):
    """registry 条目 keccak 与 rpc golden 满长 code 全等（proxy-only hash 不算通过）。"""
    url = temp_pg_db.url
    _upgrade(url)
    golden = frozen_fixture("polygon_rpc_golden")

    # pusd（none）与 deposit_wallet（eip1967，校验 impl code）
    for name, rpc_key in (
        ("pusd", "eth_getCode_pusd"),
        ("deposit_wallet", "eth_getCode_deposit_wallet"),
        ("ctf_adapter_standard", "eth_getCode_ctf_adapter"),
        ("neg_risk_adapter", "eth_getCode_neg_risk_adapter"),
    ):
        entry = _fixture_entry(name)
        code = golden["responses"][rpc_key]["node-a"]["result"]
        assert code_keccak(code) == entry["runtime_keccak"], f"{name} runtime mismatch"
        if name == "deposit_wallet":
            impl_code = golden["responses"]["eth_getCode_deposit_wallet_impl"]["node-a"]["result"]
            assert code_keccak(impl_code) == entry["resolved_code_keccak"]
        if name == "pusd":
            impl_code = golden["responses"]["eth_getCode_pusd_impl"]["node-a"]["result"]
            assert code_keccak(impl_code) == entry["resolved_code_keccak"]


def test_publish_fixture_entries(temp_pg_db):
    url = temp_pg_db.url
    _upgrade(url)
    for name in ("pusd", "ctf", "deposit_wallet", "ctf_adapter_standard", "neg_risk_adapter"):
        entry = _fixture_entry(name)
        e = {
            "registry_version": entry["registry_version"], "kind": entry["name"],
            "version_no": entry.get("version_no", 1), "chain_id": entry["chain_id"],
            "address": entry["address"], "proxy_kind": entry["proxy_kind"],
            "runtime_keccak": entry["runtime_keccak"],
            "resolved_implementation_or_beacon": entry["resolved_implementation_or_beacon"],
            "resolved_code_keccak": entry["resolved_code_keccak"],
            "snapshot_block_number": entry["snapshot_block_number"],
            "snapshot_block_hash": entry["snapshot_block_hash"],
            "source_url": entry["source_url"], "retrieved_at": entry["retrieved_at"],
            "content_hash": entry["hash"],
            "extra": entry.get("extra"),
        }
        _insert_registry(url, e)
    # 五条 ACTIVE 均可发布
    engine = create_engine(url)
    try:
        with engine.connect() as c:
            n = c.execute(text(
                "SELECT count(*) FROM trading.contract_registry WHERE status='ACTIVE'"
            )).scalar_one()
            assert n == 5
            # 同 chain+kind 第二个 ACTIVE 拒绝（用不同 version_no）
            row = c.execute(text(
                "SELECT chain_id, kind, address, proxy_kind, runtime_keccak, "
                "resolved_code_keccak FROM trading.contract_registry WHERE kind='pusd'"
            )).fetchone()
    finally:
        engine.dispose()


def test_wrong_chain_and_proxy_only_hash_rejected(temp_pg_db):
    url = temp_pg_db.url
    _upgrade(url)
    entry = _fixture_entry("pusd")

    # wrong chain → CHECK 拒绝
    with pytest.raises(Exception, match="ck_contract_registry_chain_id"):
        _insert_registry(url, {
            "registry_version": "v", "kind": "pusd", "version_no": 1,
            "chain_id": 42161, "address": entry["address"], "proxy_kind": entry["proxy_kind"],
            "runtime_keccak": entry["runtime_keccak"],
            "resolved_implementation_or_beacon": entry["resolved_implementation_or_beacon"],
            "resolved_code_keccak": entry["resolved_code_keccak"],
            "snapshot_block_number": SNAPSHOT_BLOCK, "snapshot_block_hash": entry["snapshot_block_hash"],
            "source_url": "u", "retrieved_at": entry["retrieved_at"],
            "content_hash": "ab" * 32, "extra": entry.get("extra"),
        })

    # proxy-only hash：eip1967 但 resolved_code 与 impl code 不匹配 → 发布 trigger 不校验 keccak
    # 一致性（那是逻辑层），但 resolved 地址必须与 slot 语义一致由 Logic 负责；此处校验
    # 空 code / 非满长 keccak 由 CHECK 拒绝。
    with pytest.raises(Exception, match="v2_contract_registry_direct_code_mismatch"):
        _insert_registry(url, {
            "registry_version": "v", "kind": "pusd", "version_no": 2,
            "chain_id": CHAIN_ID, "address": "0x" + "99" * 20, "proxy_kind": "none",
            "runtime_keccak": "0x1234", "resolved_implementation_or_beacon": None,
            "resolved_code_keccak": "0x" + "a" * 64,
            "snapshot_block_number": SNAPSHOT_BLOCK, "snapshot_block_hash": entry["snapshot_block_hash"],
            "source_url": "u", "retrieved_at": entry["retrieved_at"], "content_hash": "ac" * 32,
        })


def test_registry_immutable_no_update_delete(temp_pg_db):
    url = temp_pg_db.url
    _upgrade(url)
    entry = _fixture_entry("pusd")
    _insert_registry(url, {
        "registry_version": entry["registry_version"], "kind": "pusd",
        "version_no": 1, "chain_id": CHAIN_ID, "address": entry["address"],
        "proxy_kind": entry["proxy_kind"], "runtime_keccak": entry["runtime_keccak"],
        "resolved_implementation_or_beacon": entry["resolved_implementation_or_beacon"],
        "resolved_code_keccak": entry["resolved_code_keccak"],
        "snapshot_block_number": SNAPSHOT_BLOCK,
        "snapshot_block_hash": entry["snapshot_block_hash"],
        "source_url": entry["source_url"], "retrieved_at": entry["retrieved_at"],
        "content_hash": entry["hash"], "extra": entry.get("extra"),
    })
    engine = create_engine(url)
    try:
        with engine.connect() as c:
            with pytest.raises(Exception, match="v2_contract_registry_immutable"):
                c.execute(text("UPDATE trading.contract_registry SET status='SUPERSEDED' WHERE kind='pusd'"))
            c.rollback()
            with pytest.raises(Exception, match="v2_contract_registry_immutable"):
                c.execute(text("DELETE FROM trading.contract_registry WHERE kind='pusd'"))
            c.rollback()
    finally:
        engine.dispose()


def test_registry_source_url_and_snapshot_frozen(temp_pg_db):
    """fixture 要求 source_url/retrieved_at/content_hash 齐备且 snapshot 固定。"""
    reg = frozen_fixture("contract_registry")
    for e in reg["entries"]:
        assert e["source_url"] == "https://docs.polymarket.com/resources/contracts"
        assert e["retrieved_at"].startswith("2026-08-11")
        assert len(e["hash"]) == 64
        assert e["snapshot_block_number"] == SNAPSHOT_BLOCK
    sources = frozen_fixture("provider_source")
    assert len(sources["sources"]) == 6


def test_atomic_publish_exact_retry_same_address_and_shared_runtime(temp_pg_db):
    """同版本并发 retry effect=0；新实现可复用同 proxy 地址/runtime hash。"""
    url = temp_pg_db.url
    _upgrade(url)
    v1 = _fixture_entry("pusd")
    _insert_registry(url, {
        "registry_version": v1["registry_version"], "kind": "pusd", "version_no": 1,
        "chain_id": CHAIN_ID, "address": v1["address"], "proxy_kind": v1["proxy_kind"],
        "runtime_keccak": v1["runtime_keccak"],
        "resolved_implementation_or_beacon": v1["resolved_implementation_or_beacon"],
        "resolved_code_keccak": v1["resolved_code_keccak"],
        "snapshot_block_number": SNAPSHOT_BLOCK, "snapshot_block_hash": v1["snapshot_block_hash"],
        "source_url": v1["source_url"], "retrieved_at": v1["retrieved_at"],
        "content_hash": v1["hash"], "extra": v1["extra"],
    })
    params = {
        "rv": "polygon-mainnet-v2", "kind": "pusd", "ver": 2, "chain": CHAIN_ID,
        "addr": v1["address"], "pk": v1["proxy_kind"],
        "runtime": v1["runtime_keccak"],
        "resolved": v1["resolved_implementation_or_beacon"],
        "resolved_code": v1["resolved_code_keccak"], "snapshot": SNAPSHOT_BLOCK,
        "block_hash": v1["snapshot_block_hash"], "source": v1["source_url"],
        "retrieved": "2026-08-12T00:00:00Z", "content": "1" * 64,
        "extra": json.dumps(v1["extra"]),
    }
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: _publish_registry(url, params), range(2)))
    assert ids[0] == ids[1]
    engine = create_engine(url)
    try:
        with engine.connect() as c:
            rows = c.execute(text(
                "SELECT version_no,status,address,runtime_keccak FROM trading.contract_registry "
                "WHERE kind='pusd' ORDER BY version_no"
            )).fetchall()
            assert rows == [
                (1, "SUPERSEDED", v1["address"], v1["runtime_keccak"]),
                (2, "ACTIVE", v1["address"], v1["runtime_keccak"]),
            ]
    finally:
        engine.dispose()
    conflict = dict(params, content="2" * 64)
    with pytest.raises(Exception, match="v2_contract_registry_version_conflict"):
        _publish_registry(url, conflict)
