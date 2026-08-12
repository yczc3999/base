"""WP-06 production runtime vertical acceptance against real PostgreSQL.

The fixture goes through EvaluationRuntime -> ChainSettlementLogic -> repositories/UoW
and the official fixture-marked provider drivers.  Chain operation/state/ledger/outbox
facts are never synthesized by this test.
"""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from app.db.uow import UnitOfWork
from app.services.polymarket.geoblock_driver import GeoblockDriver, fixture_geoblock_transport
from app.services.polymarket.polygon_driver import fixture_polygon_transport
from tests.trading.integration.wp06_runtime_fixture import (
    build_runtime,
    query,
    request,
    seed_authority,
    seed_real_position_lineage,
    seed_settlement,
    upgrade,
)


async def _dispose(_runtime: Any, engine: Any) -> None:
    await engine.dispose()


def _one(url: str, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    rows = query(url, sql, params)
    assert len(rows) == 1, rows
    return dict(rows[0])


@pytest.mark.anyio
async def test_submit_unknown_recovery_finality_effect_once_and_lineage(
    temp_pg_db, tmp_path, monkeypatch
):
    """TX1 -> fake submit -> canonical finality is one durable economic effect."""
    url = temp_pg_db.url
    upgrade(url)
    ids = seed_authority(url)
    runtime, store, calls, engine = build_runtime(url, tmp_path / "cas", ids)
    ids = await seed_real_position_lineage(runtime._sessions, url, ids)
    _instrument_provider_transactions(monkeypatch, runtime, calls)
    try:
        await seed_settlement(runtime, store, ids)
        submitted = await runtime.submit_redeem(request(ids))
        assert submitted["status"] == "RELAYER_NEW"
        assert calls["submit_calls"] == 1

        finalized = await runtime.recover_chain_operation(
            operation_id=submitted["operation_id"], fencing_token=1
        )
        assert finalized["status"] == "FINALIZED"
        assert finalized["applied"] is True

        replay = await runtime.recover_chain_operation(
            operation_id=submitted["operation_id"], fencing_token=1,
            audit_finalized=True,
        )
        assert replay == {"status": "FINALIZED", "replayed": True, "applied": False}
        assert calls["submit_calls"] == 1

        op = _one(url, "SELECT operation_key,status,economic_effect_applied,"
                       "registry_evidence_artifact_id,balance_evidence_artifact_id "
                       "FROM trading.chain_operations WHERE id=:id", {"id": submitted["operation_id"]})
        assert op["operation_key"] == request(ids).operation_key
        assert op["status"] == "FINALIZED"
        assert op["economic_effect_applied"] is True
        assert all(op[key] is not None for key in (
            "registry_evidence_artifact_id", "balance_evidence_artifact_id"
        ))

        positions = query(url, "SELECT portfolio_namespace,quantity,cost_basis,version "
                               "FROM trading.positions WHERE account_id=:account ORDER BY id",
                          {"account": ids["account"]})
        assert len(positions) == 1
        assert positions[0]["portfolio_namespace"] == f"exec-{ids['account']}"
        assert positions[0]["quantity"] == 0 and positions[0]["cost_basis"] == 0

        ledger = _one(url, "SELECT id,portfolio_namespace,chain_operation_id FROM "
                           "trading.ledger_transactions WHERE chain_operation_id=:id AND kind='SETTLEMENT'",
                      {"id": submitted["operation_id"]})
        assert ledger["portfolio_namespace"] == f"exec-{ids['account']}"
        sums = query(url, "SELECT asset_type,asset_key,sum(amount) total FROM trading.ledger_postings "
                          "WHERE transaction_id=:id GROUP BY asset_type,asset_key",
                     {"id": ledger["id"]})
        assert sums and all(row["total"] == 0 for row in sums)

        outbox = _one(url, "SELECT aggregate_type,aggregate_id,idempotency_key FROM "
                           "trading.transactional_outbox WHERE topic='chain.settlement.finalized'")
        assert outbox == {
            "aggregate_type": "chain_operation",
            "aggregate_id": request(ids).operation_key,
            "idempotency_key": f"chain-settlement:{request(ids).operation_key}",
        }
        audit = query(url, "SELECT event_type,aggregate_id FROM trading.workflow_events "
                           "WHERE aggregate_type='chain_operation' AND aggregate_id=:key",
                      {"key": request(ids).operation_key})
        assert {row["event_type"] for row in audit} >= {
            "CHAIN_RECOVERY_OBSERVATION", "SETTLEMENT_FINALIZED"
        }

        attempts = query(url, "SELECT driver,endpoint,request_hash,response_hash,fence_token "
                              "FROM trading.external_call_attempts ORDER BY id")
        assert {(r["driver"], r["endpoint"]) for r in attempts} >= {
            ("geoblock", "check"), ("polygon", "registry_bundle"),
            ("polygon", "pre_balances"), ("relayer", "prepare_batch"),
            ("relayer", "submit"), ("relayer", "status_nonce"),
            ("polygon", "receipt_finality_balance"),
        }
        assert all(len(r["request_hash"]) == len(r["response_hash"]) == 64 for r in attempts)
        assert all(r["fence_token"] == 1 for r in attempts)
        recovery = next(
            row for row in query(
                url, "SELECT payload FROM trading.workflow_events "
                     "WHERE event_type='CHAIN_RECOVERY_OBSERVATION' ORDER BY id DESC"
            ) if row["payload"].get("provider_artifact_hash")
        )
        artifact_ids = {op["registry_evidence_artifact_id"], op["balance_evidence_artifact_id"]}
        objects = query(url, "SELECT id,sha256,locator FROM trading.artifact_objects WHERE id=ANY(:ids)",
                        {"ids": list(artifact_ids)})
        assert {r["id"] for r in objects} == artifact_ids
        assert all(r["sha256"] in r["locator"] for r in objects)
        assert query(url, "SELECT id FROM trading.artifact_objects WHERE sha256=:sha",
                     {"sha": recovery["payload"]["provider_artifact_hash"]})
        assert calls["session_in_tx"] and not any(calls["session_in_tx"])
    finally:
        await _dispose(runtime, engine)


def _instrument_provider_transactions(monkeypatch, runtime: Any, calls: dict[str, Any]) -> None:
    """Record the actual AsyncSession transaction state at every fake provider call."""
    active: ContextVar[tuple[UnitOfWork, ...]] = ContextVar("wp06_active_uow", default=())
    calls["session_in_tx"] = []
    original_enter = UnitOfWork.__aenter__
    original_exit = UnitOfWork.__aexit__

    async def enter(uow):
        entered = await original_enter(uow)
        uow._wp06_test_token = active.set((*active.get(), uow))
        return entered

    async def exit_(uow, exc_type, exc, tb):
        try:
            return await original_exit(uow, exc_type, exc, tb)
        finally:
            active.reset(uow._wp06_test_token)

    monkeypatch.setattr(UnitOfWork, "__aenter__", enter)
    monkeypatch.setattr(UnitOfWork, "__aexit__", exit_)

    def observe() -> None:
        states = [
            bool(uow._session.in_transaction())
            for uow in active.get()
            if uow._session is not None
        ]
        # An empty active set is the strongest form of the invariant. Retain one
        # explicit False sample per call so the acceptance evidence is countable.
        calls["session_in_tx"].append(any(states) if states else False)
        assert not any(states), states

    polygon_transport = runtime._polygon._transport

    @fixture_polygon_transport
    async def polygon(payload, endpoint):
        observe()
        return await polygon_transport(payload, endpoint)

    relayer_transport = runtime._relayer._transport
    from app.services.polymarket.relayer_driver import fixture_relayer_transport

    @fixture_relayer_transport
    async def relayer(method, path, **kwargs):
        observe()
        return await relayer_transport(method, path, **kwargs)

    geo_transport = runtime._geoblock._transport

    @fixture_geoblock_transport
    async def geoblock():
        observe()
        return await geo_transport()

    runtime._polygon._transport = polygon
    runtime._relayer._transport = relayer
    runtime._geoblock._transport = geoblock


def _publish_adapter_successor(url: str, ids: dict[str, Any]) -> None:
    old = ids["entries"]["ctf_adapter_standard"]
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "SELECT trading.v2_publish_contract_registry("
                ":registry,:kind,2,137,:address,:proxy,:runtime,:resolved,:resolved_code,"
                ":block_no,:block_hash,:source,:retrieved,:content,CAST(:extra AS jsonb))"
            ), {
                "registry": old["registry_version"] + "/successor",
                "kind": "ctf_adapter_standard", "address": old["address"],
                "proxy": old["proxy_kind"], "runtime": old["runtime_keccak"],
                "resolved": old.get("resolved_implementation_or_beacon"),
                "resolved_code": old["resolved_code_keccak"],
                "block_no": old["snapshot_block_number"], "block_hash": old["snapshot_block_hash"],
                "source": old["source_url"], "retrieved": datetime.now(timezone.utc),
                "content": "9" * 64, "extra": __import__("json").dumps(old.get("extra") or {}),
            })
    finally:
        engine.dispose()


def _open_reconciliation_and_kill(url: str, account_id: int) -> None:
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.begin() as connection:
            # Published controls are immutable. Rotate to an explicitly killed
            # permission/release instead of weakening that production guard.
            lineage = connection.execute(text(
                "SELECT r.config_version_id,r.strategy_version_id,r.execution_spec_version_id,"
                "p.mode,p.capability,p.limits,p.evaluation_capital,p.authorized_capital "
                "FROM trading.pm_accounts a JOIN trading.release_manifests r "
                "ON r.id=a.release_manifest_id JOIN trading.capital_permission_manifests p "
                "ON p.id=a.capital_permission_manifest_id WHERE a.id=:account"
            ), {"account": account_id}).mappings().one()
            permission = connection.execute(text(
                "INSERT INTO trading.capital_permission_manifests(name,mode,capability,limits,"
                "evaluation_capital,authorized_capital,kill_switch,content_hash,status) "
                "VALUES(:name,:mode,CAST(:cap AS jsonb),CAST(:limits AS jsonb),:evaluation,"
                ":authorized,true,:hash,'active') RETURNING id"
            ), {
                "name": f"wp06-killed-{account_id}", "mode": lineage["mode"],
                "cap": __import__("json").dumps(lineage["capability"]),
                "limits": __import__("json").dumps(lineage["limits"]),
                "evaluation": lineage["evaluation_capital"],
                "authorized": lineage["authorized_capital"], "hash": "b" * 64,
            }).scalar_one()
            release = connection.execute(text(
                "INSERT INTO trading.release_manifests(release_name,config_version_id,"
                "strategy_version_id,execution_spec_version_id,capital_permission_manifest_id,"
                "git_sha,image_digest,db_revision,total_hash,status) VALUES(:name,:cfg,:strategy,"
                ":spec,:permission,:git,'fixture-killed','b1000052',:hash,'active') RETURNING id"
            ), {
                "name": f"wp06-killed-{account_id}", "cfg": lineage["config_version_id"],
                "strategy": lineage["strategy_version_id"],
                "spec": lineage["execution_spec_version_id"], "permission": permission,
                "git": "c" * 64, "hash": "d" * 64,
            }).scalar_one()
            connection.execute(text(
                "UPDATE trading.pm_accounts SET release_manifest_id=:release,"
                "capital_permission_manifest_id=:permission WHERE id=:account"
            ), {"release": release, "permission": permission, "account": account_id})
            connection.execute(text(
                "INSERT INTO trading.account_reconciliations(reconciliation_key,account_id,trigger_reason,"
                "ws_watermark,rest_page_cursor,rest_page_hash,unknown_queries,input_manifest_hash,"
                "differences,fencing_token,status) VALUES(:key,:account,'restart',0,'{}',:h,'{}',:h,"
                "'[]',1,'RECONCILING')"
            ), {"key": f"wp06-recovery-{account_id}", "account": account_id, "h": "a" * 64})
    finally:
        engine.dispose()


@pytest.mark.anyio
async def test_concurrent_duplicate_submit_has_one_transport_owner(temp_pg_db, tmp_path, monkeypatch):
    url = temp_pg_db.url
    upgrade(url)
    ids = seed_authority(url)
    runtime, store, calls, engine = build_runtime(url, tmp_path / "cas", ids)
    ids = await seed_real_position_lineage(runtime._sessions, url, ids)
    _instrument_provider_transactions(monkeypatch, runtime, calls)
    try:
        await seed_settlement(runtime, store, ids)
        # Parallel tasks may legitimately occupy separate pool connections. The
        # context-local session check remains the governing boundary assertion.
        calls["enforce_no_tx"] = False
        left, right = await asyncio.gather(
            runtime.submit_redeem(request(ids, "concurrent")),
            runtime.submit_redeem(request(ids, "concurrent")),
        )
        assert left["operation_id"] == right["operation_id"]
        assert calls["submit_calls"] == 1
        assert query(url, "SELECT id FROM trading.chain_operations") == [{"id": left["operation_id"]}]
        assert query(url, "SELECT id FROM trading.idempotency_claims "
                          "WHERE scope='chain_operation'")
        assert calls["session_in_tx"] and not any(calls["session_in_tx"])
    finally:
        await _dispose(runtime, engine)


@pytest.mark.anyio
async def test_sent_operation_recovers_after_kill_reconciliation_and_registry_rollover(
    temp_pg_db, tmp_path, monkeypatch
):
    """Current write authority may close, while frozen TX1 remains read-recoverable."""
    url = temp_pg_db.url
    upgrade(url)
    ids = seed_authority(url)
    runtime, store, calls, engine = build_runtime(url, tmp_path / "cas", ids)
    ids = await seed_real_position_lineage(runtime._sessions, url, ids)
    _instrument_provider_transactions(monkeypatch, runtime, calls)
    try:
        await seed_settlement(runtime, store, ids)
        submitted = await runtime.submit_redeem(request(ids, "restart"))
        assert submitted["status"] == "RELAYER_NEW"
        _open_reconciliation_and_kill(url, ids["account"])
        _publish_adapter_successor(url, ids)

        recovered = await runtime.recover_chain_operation(
            operation_id=submitted["operation_id"], fencing_token=1
        )
        assert recovered["status"] == "FINALIZED" and recovered["applied"] is True
        assert calls["submit_calls"] == 1
        states = query(url, "SELECT transition_to FROM trading.chain_operation_state_history "
                            "WHERE operation_id=:id ORDER BY sequence_no", {"id": submitted["operation_id"]})
        assert states[-1]["transition_to"] == "FINALIZED"
        registry = query(url, "SELECT version_no,status FROM trading.contract_registry "
                              "WHERE kind='ctf_adapter_standard' ORDER BY version_no")
        assert registry == [
            {"version_no": 1, "status": "SUPERSEDED"},
            {"version_no": 2, "status": "ACTIVE"},
        ]
        assert not any(calls["session_in_tx"])
    finally:
        await _dispose(runtime, engine)


@pytest.mark.anyio
async def test_post_final_removed_receipt_becomes_conflict_without_second_effect(
    temp_pg_db, tmp_path
):
    url = temp_pg_db.url
    upgrade(url)
    ids = seed_authority(url)
    runtime, store, calls, engine = build_runtime(url, tmp_path / "cas", ids)
    ids = await seed_real_position_lineage(runtime._sessions, url, ids)
    try:
        await seed_settlement(runtime, store, ids)
        submitted = await runtime.submit_redeem(request(ids, "contradiction"))
        await runtime.recover_chain_operation(operation_id=submitted["operation_id"], fencing_token=1)
        original = runtime._polygon._transport

        @fixture_polygon_transport
        async def removed(payload, endpoint):
            response = await original(payload, endpoint)
            if payload["method"] == "eth_getTransactionReceipt" and response.get("result"):
                response = __import__("copy").deepcopy(response)
                response["result"]["logs"] = [{"removed": True}]
            return response

        runtime._polygon._transport = removed
        conflict = await runtime.recover_chain_operation(
            operation_id=submitted["operation_id"], fencing_token=1, audit_finalized=True
        )
        assert conflict == {
            "status": "SETTLEMENT_CONFLICT", "replayed": False, "applied": False
        }
        assert calls["submit_calls"] == 1
        assert len(query(url, "SELECT id FROM trading.ledger_transactions "
                              "WHERE chain_operation_id=:id AND kind='SETTLEMENT'",
                         {"id": submitted["operation_id"]})) == 1
        assert len(query(url, "SELECT id FROM trading.transactional_outbox "
                              "WHERE topic='chain.settlement.finalized'")) == 1
        alert = _one(url, "SELECT severity,code FROM trading.alert_events "
                          "WHERE code='CHAIN_FINALITY_CONTRADICTION'")
        assert alert == {"severity": "CRITICAL", "code": "CHAIN_FINALITY_CONTRADICTION"}
    finally:
        await _dispose(runtime, engine)


@pytest.mark.anyio
async def test_cancelled_after_send_persists_unknown_then_rethrows(temp_pg_db, tmp_path):
    url = temp_pg_db.url
    upgrade(url)
    ids = seed_authority(url)
    runtime, store, calls, engine = build_runtime(url, tmp_path / "cas", ids)
    ids = await seed_real_position_lineage(runtime._sessions, url, ids)
    try:
        await seed_settlement(runtime, store, ids)
        original = runtime._relayer._transport
        from app.services.polymarket.relayer_driver import fixture_relayer_transport

        @fixture_relayer_transport
        async def cancel_after_send(method, path, **kwargs):
            response = await original(method, path, **kwargs)
            if path == "/submit":
                raise asyncio.CancelledError
            return response

        runtime._relayer._transport = cancel_after_send
        with pytest.raises(asyncio.CancelledError):
            await runtime.submit_redeem(request(ids, "cancelled"))
        op = _one(url, "SELECT status,transaction_id,transaction_hash,economic_effect_applied "
                       "FROM trading.chain_operations WHERE operation_key=:key",
                  {"key": request(ids, "cancelled").operation_key})
        assert op == {
            "status": "UNKNOWN", "transaction_id": None, "transaction_hash": None,
            "economic_effect_applied": False,
        }
        assert calls["submit_calls"] == 1
        assert not query(url, "SELECT id FROM trading.ledger_transactions WHERE kind='SETTLEMENT'")
        assert not query(url, "SELECT id FROM trading.transactional_outbox "
                              "WHERE topic='chain.settlement.finalized'")
    finally:
        await _dispose(runtime, engine)


@pytest.mark.anyio
@pytest.mark.parametrize("case", ["denied", "stale", "malformed"])
async def test_geoblock_fail_closed_before_nonce_sign_send_with_durable_evidence(
    temp_pg_db, tmp_path, case
):
    url = temp_pg_db.url
    upgrade(url)
    ids = seed_authority(url)
    runtime, store, calls, engine = build_runtime(url, tmp_path / "cas", ids)
    ids = await seed_real_position_lineage(runtime._sessions, url, ids)
    try:
        await seed_settlement(runtime, store, ids)
        now = datetime.now(timezone.utc)

        @fixture_geoblock_transport
        async def geo():
            if case == "denied":
                return {"allowed": False, "observed_at": now.isoformat(),
                        "region_code": "CA", "source_version": "fixture-geoblock/v1"}
            if case == "stale":
                return {"allowed": True, "observed_at": (now - timedelta(minutes=2)).isoformat(),
                        "region_code": "CA", "source_version": "fixture-geoblock/v1"}
            return {"allowed": "yes", "observed_at": now.isoformat(),
                    "region_code": "CA", "source_version": "fixture-geoblock/v1"}

        runtime._geoblock = GeoblockDriver(transport=geo, now_provider=lambda: now)
        before_relayer = runtime._relayer.transport_calls
        before_polygon = runtime._polygon.transport_calls
        before_artifacts = _one(
            url, "SELECT count(*) AS n FROM trading.artifact_objects"
        )["n"]
        with pytest.raises(RuntimeError, match="geoblock"):
            await runtime.submit_redeem(request(ids, f"geo-{case}"))
        assert runtime._relayer.transport_calls == before_relayer
        assert runtime._polygon.transport_calls == before_polygon
        assert calls["submit_calls"] == 0
        assert not query(url, "SELECT id FROM trading.chain_operations")

        attempts = query(url, "SELECT driver,endpoint,error_reason,response_hash FROM "
                              "trading.external_call_attempts WHERE driver='geoblock'")
        assert len(attempts) == 1
        assert attempts[0]["endpoint"] == "check"
        assert attempts[0]["error_reason"]
        evidence = query(url, "SELECT id,sha256,locator FROM trading.artifact_objects "
                              "WHERE mime='application/json' ORDER BY id")
        assert len(evidence) == before_artifacts + 1
        assert evidence[-1]["sha256"] in evidence[-1]["locator"]
    finally:
        await _dispose(runtime, engine)
