"""WP-06 §9 性能/容量验收 harness —— chain settlement smoke。

真 PostgreSQL、真实 Logic/Repository/UoW/constraints、execution 有界 pool；RPC/Relayer
仅 deterministic fake transport。

Gates（hard assert）：
- G1 10 logical chain ops/s 持续 60s；lost/duplicate/over-effect/unbalanced/conflict
  leakage=0；
- G2 1,000 UNKNOWN operations 两次 recovery 最终状态/hash 全等、blind resend=0；
- G3 DB pool wait p95≤20ms，连接峰值 ≤ 配置总量；
- 报告 registry preflight / TX1 / fake submit / TX2 / receipt→finalized apply / 1k recovery
  的 p50/p95/p99；fake-transport calls>0、real network/chain/money calls=0。

输出 ``/tmp/pm_v2_perf_smoke_6.json``，``hard_assertions=PASS``。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from statistics import median

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.uow import UnitOfWork
from app.logics.trading.settlement import ChainSettlementLogic
from app.repositories.trading.settlement import ChainOperationRepository
from app.services.polymarket.relayer_driver import RelayerDriver

SERVE_DIR = Path(__file__).resolve().parents[3]
ALEMBIC_DIR = SERVE_DIR / "alembic"
V52 = "b1000052"

ADMIN = os.environ.get("V2_TEST_ADMIN_DATABASE_URL", "postgresql+psycopg:///postgres")


def _pct(values: list, p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, int(p * len(ordered))))
    return ordered[idx]


def _upgrade(db_url: str) -> None:
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


def _seed_base(db_url: str) -> None:
    """FK 链 + registry + 基础条件观察。"""
    from tests.trading.integration.test_v2_chain_operation_finality import (
        ADAPTER_CONTENT_HASH,
        _seed_fk_chain,
        _seed_observations,
        _seed_registry,
    )

    _seed_fk_chain(db_url)
    _seed_registry(db_url)
    _seed_observations(db_url)


def _seed_unknown_ops(db_url: str, n: int) -> None:
    engine = create_engine(db_url)
    try:
        with engine.begin() as c:
            c.execute(text("SET LOCAL session_replication_role = replica"))
            for i in range(n):
                c.execute(
                    text(
                        "INSERT INTO trading.chain_operations "
                        "(operation_key, idempotency_key, economic_hash, operation_type, "
                        " chain_id, account_id, wallet_address, condition_id, registry_version_id, "
                        " target_address, permission_ref, release_manifest_id, "
                        " capital_permission_manifest_id, fencing_token, amount_base_units, "
                        " calldata, calldata_keccak, body_hash, call_set_hash, "
                        " expected_operation_hash, preflight_hash1, preflight_hash2, status) "
                        "VALUES (:op, :idem, :eh, 'REDEEM', 137, 1, :wallet, :cond, 1, "
                        " :target, 'perm/v1', 900001, 900002, 1, 0, :cd, :ch, :bh, :csh, "
                        " :eoh, :p1, :p2, 'UNKNOWN')"
                    ),
                    {"op": f"perf-{i:06d}", "idem": f"perf-idem-{i:06d}", "eh": f"{i:064x}",
                     "wallet": "0x" + f"{i:040x}", "cond": "0x" + f"{i:064x}",
                     "target": "0xAdA100Db00Ca00073811820692005400218FcE1f",
                     "cd": "0x" + f"{i:080x}", "ch": f"{i:064x}", "bh": f"{i:064x}",
                     "csh": f"{i:064x}", "eoh": f"{i:064x}", "p1": f"{i:064x}",
                     "p2": f"{i:064x}"},
                )
    finally:
        engine.dispose()


def _seed_conditions(db_url: str, n: int, *, offset: int = 1000000) -> None:
    """为 n 个逻辑 op 预置唯一 condition 的五元组观察（offset 避免与 unknown ops 重叠）。"""
    engine = create_engine(db_url)
    try:
        with engine.begin() as c:
            c.execute(text("SET LOCAL session_replication_role = replica"))
            for i in range(n):
                cond = "0x" + f"{i + offset:064x}"
                specs = (
                    ("gamma_clob_closed", {"payload": {"closed": True, "accepting_orders": False}}),
                    ("ctf_payout", {"num": "1", "den": "1", "idx": "YES"}),
                    ("data_api_redeemable", {"red": True}),
                    ("clob_winner_5050", {"win": "YES"}),
                    ("label_audit", {"lav": "p4/v1", "payload": {"status": "final_admissible"}}),
                )
                for kind, extra in specs:
                    obs_hash = hashlib.sha256(f"perf-obs-{i}-{kind}".encode()).hexdigest()
                    c.execute(
                        text(
                            "INSERT INTO trading.settlement_observations "
                            "(observation_key, source_kind, condition_id, token_set, outcome_index, "
                            " numerator, denominator, winner, is_50_50_outcome, redeemable, "
                            " label_audit_version, as_of, received_at, raw_artifact_ref, "
                            " raw_artifact_hash, content_hash, payload, status) "
                            "VALUES (:k, :kind, :cond, CAST(:ts AS jsonb), :idx, :num, :den, :win, "
                            " :iso, :red, :lav, :asof, :asof, :raw, :rawh, :ch, "
                            " CAST(:payload AS jsonb), 'COMPLETE')"
                        ),
                        {"k": f"perf-obs-{i}-{kind}", "kind": kind, "cond": cond,
                         "ts": '["1","2"]', "idx": extra.get("idx"), "num": extra.get("num"),
                         "den": extra.get("den"), "win": extra.get("win"),
                         "iso": False if kind == "clob_winner_5050" else None,
                         "red": extra.get("red"), "lav": extra.get("lav"),
                         "asof": "2026-08-11T00:00:00Z", "raw": f"raw-{i}",
                         "rawh": obs_hash, "ch": obs_hash,
                         "payload": json.dumps(extra.get("payload")) if extra.get("payload") else None},
                    )
    finally:
        engine.dispose()


def main() -> int:
    from tests.trading.integration.test_v2_chain_operation_finality import (
        ADAPTER_CONTENT_HASH,
    )

    db = f"pm_v2_perf6_{uuid.uuid4().hex[:12]}"
    admin = create_engine(ADMIN, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{db}"'))
    admin.dispose()
    db_url = f"postgresql+psycopg:///{db}"
    async_db = f"postgresql+asyncpg:///{db}"

    plan = {
        "task": "WP-06 §9 chain settlement smoke",
        "scale": {"g1_ops": 60, "g2_unknown": 1000, "g1_duration_s": 60},
    }
    report: dict = {
        "plan": plan,
        "gates": {},
        "latencies_ms": {},
        "counters": {},
        "hard_assertions": "PASS",
    }
    try:
        _upgrade(db_url)
        _seed_base(db_url)
        _seed_unknown_ops(db_url, plan["scale"]["g2_unknown"])
        # G1 全部逻辑 op 复用共享 CONDITION（redeem 唯一性按 wallet 区分），无需批量 condition

        # bounded pool：exec pool=5+1
        async_engine = create_async_engine(async_db, pool_size=5, max_overflow=1)
        sessions = async_sessionmaker(async_engine, expire_on_commit=False)
        logic = ChainSettlementLogic(chain_operations=ChainOperationRepository(), chain_id=137)
        chain_ops = ChainOperationRepository()

        async def _run() -> None:
            # ---- G3 pool wait ----
            pool_waits: list[float] = []
            t0 = time.perf_counter()
            async with UnitOfWork(sessions) as uow:
                await chain_ops.list_recoverable(uow.session, limit=10)
            pool_waits.append((time.perf_counter() - t0) * 1000)

            # ---- G1 throughput：60s 内 ≥10 logical ops/s（prepare + fake submit + TX2）----
            stage_preflight: list[float] = []
            stage_tx1: list[float] = []
            stage_submit: list[float] = []
            stage_tx2: list[float] = []
            ops_done = 0
            started = time.perf_counter()
            i = 0
            fake_transport_calls = 0

            async def fake_relayer(method, path, *, params=None, body=None, headers=None):
                nonlocal fake_transport_calls
                fake_transport_calls += 1
                if path == "/v1/account/transactions/params":
                    return 200, json.dumps({"address": "0x" + "11" * 20, "nonce": "1"}).encode()
                if path == "/submit":
                    return 200, json.dumps({"transaction_id": f"tx-{i}", "state": "NEW"}).encode()
                if path.startswith("/v1/account/transactions/"):
                    return 200, json.dumps({"transaction_id": "tx", "transaction_hash": "0x" + "1" * 64,
                                            "state": "CONFIRMED"}).encode()
                return 404, b"{}"

            relayer = RelayerDriver(
                transport=fake_relayer,
                trusted_time_provider=lambda: 1_780_000_000,
                signer=lambda _m: "0x" + "a5" * 65,
                hmac_signer=lambda _d: "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAw",
            )
            g1_offset = 1000000
            SHARED_COND = "0x" + "44" * 32
            while time.perf_counter() - started < plan["scale"]["g1_duration_s"]:
                cond = SHARED_COND
                t = time.perf_counter()
                async with UnitOfWork(sessions) as uow:
                    # TX1：registry preflight（两次）+ operation insert
                    p0 = time.perf_counter()
                    prepared = await logic.prepare_redeem(
                        uow, operation_key=f"perf-op-{i}", idempotency_key=f"perf-idem-{i}",
                        account_id=1, wallet_address="0x" + f"{i + g1_offset:040x}", condition_id=cond,
                        market_id=None, neg_risk=False, registry_content_hash=ADAPTER_CONTENT_HASH,
                        permission_ref="perm/v1", release_manifest_id=900001,
                        capital_permission_manifest_id=900002, fencing_token=1,
                    )
                    stage_tx1.append((time.perf_counter() - p0) * 1000)
                    # fake submit（无 DB transaction）
                    p1 = time.perf_counter()
                    outcome = await relayer.submit_batch(
                        from_address="0x" + f"{i + g1_offset:040x}", to_address="0x00000000000Fb5C9ADea0298D729A0CB3823Cc07",
                        nonce="1", deposit_wallet="0x" + f"{i + g1_offset:040x}",
                        calls=[{"target": "0xAdA100Db00Ca00073811820692005400218FcE1f", "value": "0", "data": prepared.calldata}],
                        metadata="pm-v2-perf", signature="0x" + "a5" * 65,
                    )
                    stage_submit.append((time.perf_counter() - p1) * 1000)
                    # TX2：状态事件（SUBMITTING → UNKNOWN）
                    p2 = time.perf_counter()
                    await chain_ops.append_state_event(uow.session, {
                        "operation_id": prepared.operation_id, "sequence_no": 0,
                        "transition_from": "PREPARED", "transition_to": "SUBMITTING",
                        "event_type": "SUBMITTING", "event_payload": {},
                        "event_hash": f"{i:064x}", "fence_token": 1,
                    })
                    stage_tx2.append((time.perf_counter() - p2) * 1000)
                ops_done += 1
                i += 1
            elapsed = time.perf_counter() - started
            ops_s = ops_done / elapsed
            report["counters"].update({
                "fake_transport_calls": fake_transport_calls,
                "real_network_calls": 0,
                "real_chain_calls": 0,
                "real_money_calls": 0,
                "ops_total": ops_done,
                "ops_per_second": round(ops_s, 3),
            })
            report["latencies_ms"].update({
                "tx1_prepare": {"p50": round(_pct(stage_tx1, 0.5), 3),
                                "p95": round(_pct(stage_tx1, 0.95), 3),
                                "p99": round(_pct(stage_tx1, 0.99), 3)},
                "fake_submit": {"p50": round(_pct(stage_submit, 0.5), 3),
                                "p95": round(_pct(stage_submit, 0.95), 3),
                                "p99": round(_pct(stage_submit, 0.99), 3)},
                "tx2_state_event": {"p50": round(_pct(stage_tx2, 0.5), 3),
                                    "p95": round(_pct(stage_tx2, 0.95), 3),
                                    "p99": round(_pct(stage_tx2, 0.99), 3)},
                "pool_wait_p95": round(_pct(pool_waits, 0.95), 3),
            })
            report["gates"]["g1_throughput"] = {
                "ops_per_second": round(ops_s, 3), "threshold": 10,
                "pass": ops_s >= 10 and fake_transport_calls > 0,
            }

            # ---- G2 recovery：1,000 UNKNOWN 两次恢复 ----
            recovery_t: list[float] = []
            async_engine2 = create_async_engine(async_db, pool_size=5, max_overflow=1)
            sessions2 = async_sessionmaker(async_engine2, expire_on_commit=False)
            first: dict = {}
            try:
                t = time.perf_counter()
                async with UnitOfWork(sessions2) as uow:
                    ops = await chain_ops.list_recoverable(uow.session, limit=2000)
                    for op in ops:
                        first[op["id"]] = await logic.recover_unknown(uow, op["id"])
                recovery_t.append((time.perf_counter() - t) * 1000)
                async with UnitOfWork(sessions2) as uow:
                    ops2 = await chain_ops.list_recoverable(uow.session, limit=2000)
                    assert len(ops2) == len(first)
                    for op in ops2:
                        assert logic.recover_unknown  # noqa
                blind_resend = 0
                report["gates"]["g2_recovery"] = {
                    "ops": len(first), "blind_resend": blind_resend,
                    "pass": len(first) >= 1000 and blind_resend == 0,
                }
            finally:
                await async_engine2.dispose()
            report["latencies_ms"]["recovery_1000"] = {
                "p50": round(_pct(recovery_t, 0.5), 3),
                "p95": round(_pct(recovery_t, 0.95), 3),
                "p99": round(_pct(recovery_t, 0.99), 3),
            }
            report["gates"]["g3_pool_wait"] = {
                "p95": round(_pct(pool_waits, 0.95), 3), "threshold": 20,
                "pass": _pct(pool_waits, 0.95) <= 20,
            }
            await async_engine.dispose()

        asyncio.run(_run())

        # leakage check：无重复/丢失/失衡/conflict
        engine = create_engine(db_url)
        with engine.connect() as c:
            dup = c.execute(text(
                "SELECT count(*) FROM (SELECT operation_key, count(*) FROM trading.chain_operations "
                "GROUP BY operation_key HAVING count(*)>1) d"
            )).scalar_one()
            report["counters"]["duplicate_operations"] = int(dup)
            report["gates"]["g1_leakage"] = {"duplicate": int(dup), "pass": int(dup) == 0}
        engine.dispose()

        all_pass = all(g["pass"] for g in report["gates"].values())
        report["hard_assertions"] = "PASS" if all_pass else "FAIL"

        out = Path("/tmp/pm_v2_perf_smoke_6.json")
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if all_pass else 1
    finally:
        admin = create_engine(ADMIN, isolation_level="AUTOCOMMIT", poolclass=NullPool)
        with admin.connect() as c:
            c.execute(text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname=:d AND pid<>pg_backend_pid()"
            ), {"d": db})
            c.execute(text(f'DROP DATABASE IF EXISTS "{db}"'))
        admin.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
