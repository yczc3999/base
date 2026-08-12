"""WP-06 §9 chain-settlement vertical performance/correctness smoke.

This is deliberately a production-path harness, not a data generator disguised as a
test.  PostgreSQL is real; artifacts use the real local CAS; operations, observations,
state history, ledger and outbox are created only by ``EvaluationRuntime`` and the
production Logic/Repository/UoW stack.  Polygon, Relayer and geoblock are deterministic
fixture transports and assert that their call runs outside the caller's DB transaction.

Normal mode runs the contractual 60 second / 1,000 UNKNOWN workload.  Set
``PM_V2_PERF_QUICK=1`` for a short plumbing check (the JSON records the reduced plan).
The machine-readable result is always written to ``/tmp/pm_v2_perf_smoke_6.json``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import resource
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from collections import defaultdict
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from alembic import command
from alembic.config import Config
from eth_account import Account
from eth_utils import keccak
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

SERVE_DIR = Path(__file__).resolve().parents[3]
if str(SERVE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVE_DIR))

from app.config import Settings
from app.db.uow import UnitOfWork
from app.domain.trading.hashing import canonical_hash
from app.repositories.trading.market_stream import MarketStreamRepository
from app.schemas.trading.settlement import (
    ChainRedeemRequest,
    ChainSettlementEvidenceInput,
    SettlementSourceArtifact,
)
from app.services.artifact_store import ArtifactStore
from app.services.artifact_store.drivers.local import LocalArtifactDriver
from app.services.polymarket.geoblock_driver import (
    GeoblockDriver,
    fixture_geoblock_transport,
)
from app.services.polymarket.polygon_driver import PolygonDriver, fixture_polygon_transport
from app.services.polymarket.relayer_driver import RelayerDriver, fixture_relayer_transport
from runtimes.trading.evaluation import EvaluationRuntime

ALEMBIC_DIR = SERVE_DIR / "alembic"
REPORT_PATH = Path("/tmp/pm_v2_perf_smoke_6.json")
ADMIN_URL = os.environ.get(
    "V2_TEST_ADMIN_DATABASE_URL", "postgresql+psycopg:///postgres"
)
RUNTIME_IDENTITY = "wp06-runtime"
CONDITION = "0x" + "77" * 32
TOKEN_YES = "1000001"
TOKEN_NO = "1000002"
SNAPSHOT_NUMBER = 91_842_167
SNAPSHOT_HASH = "0x" + "ab" * 32
RECEIPT_NUMBER = SNAPSHOT_NUMBER + 4
RECEIPT_HASH = "0x" + "ef" * 32
FINALIZED_NUMBER = RECEIPT_NUMBER + 64
FINALIZED_HASH = "0x" + "cd" * 32
CODE = "0x6000"
CODE_HASH = "0x" + keccak(bytes.fromhex(CODE[2:])).hex()
PUSD = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
DEPOSIT = "0x00000000000Fb5C9ADea0298D729A0CB3823Cc07"
ADAPTER = "0xAdA100Db00Ca00073811820692005400218FcE1f"
REGISTRY_ADDRESSES = {
    "pusd": PUSD,
    "ctf": CTF,
    "deposit_wallet": DEPOSIT,
    "ctf_adapter_standard": ADAPTER,
}
REGISTRY_HASHES = {
    "pusd": "2a45596924268153acef218ee104ed69f355399701725f88aea967438f91bd4d",
    "ctf": "6fe9adbfbb71a1d191341218da25185e0871a61b76df2758b0873d884f87cc5b",
    "deposit_wallet": "774caf5580ff19b5fefbf7fd8236885e353f4ac15803449d921aaa2a02d9511f",
    "ctf_adapter_standard": "9668ef928815b964220d02ef2562f2cf5713e9f06bf7c475b0011bae89af8741",
}

# Provider transports consult this task-local probe.  Engine transaction events set
# it on begin and clear it on commit/rollback, so a driver invoked inside a UoW fails.
_TX_DEPTH: ContextVar[int] = ContextVar("wp06_perf_tx_depth", default=0)


def _pct(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * percentile)))
    return ordered[index]


def _summary(values: list[float]) -> dict[str, float]:
    return {
        "p50": round(_pct(values, 0.50), 3),
        "p95": round(_pct(values, 0.95), 3),
        "p99": round(_pct(values, 0.99), 3),
    }


def _git_state() -> dict[str, Any]:
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=SERVE_DIR, text=True,
        capture_output=True, check=True,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain"], cwd=SERVE_DIR, text=True,
        capture_output=True, check=True,
    ).stdout.splitlines()
    return {"commit": sha, "dirty": bool(dirty), "dirty_paths": dirty}


def _upgrade(db_url: str) -> None:
    cfg = Config()
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    engine = create_engine(db_url, poolclass=NullPool)
    connection = engine.connect()
    cfg.attributes["connection"] = connection
    try:
        command.upgrade(cfg, "b1000052")
    finally:
        connection.close()
        engine.dispose()


def _create_database() -> tuple[str, str]:
    name = f"pm_v2_perf6_{uuid.uuid4().hex[:12]}"
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        admin.dispose()
    sync_url = make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)
    async_url = make_url(sync_url).set(
        drivername="postgresql+asyncpg"
    ).render_as_string(hide_password=False)
    return sync_url, async_url


def _drop_database(sync_url: str) -> bool:
    parsed = make_url(sync_url)
    name = parsed.database
    admin_url = parsed.set(database=make_url(ADMIN_URL).database).render_as_string(
        hide_password=False
    )
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    try:
        with admin.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname=:name AND pid<>pg_backend_pid()"
                ),
                {"name": name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
            return not bool(
                connection.execute(
                    text("SELECT 1 FROM pg_database WHERE datname=:name"), {"name": name}
                ).first()
            )
    finally:
        admin.dispose()


async def _register_artifact(
    sessions: async_sessionmaker, store: ArtifactStore, material: Any
) -> tuple[int, str]:
    payload = json.dumps(
        material, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    ref = await asyncio.to_thread(
        store.put_bytes, payload, "application/json", "none"
    )
    async with UnitOfWork(sessions) as uow:
        artifact_id = await MarketStreamRepository().register_artifact(uow.session, ref)
    return artifact_id, ref.sha256


async def _seed_authority(
    sync_url: str,
    sessions: async_sessionmaker,
    store: ArtifactStore,
    *,
    accounts: int,
) -> dict[str, Any]:
    """Seed only upstream authority/current projections; never chain facts."""
    snapshot_artifact_id, _ = await _register_artifact(
        sessions, store, {"fixture": "wp06-perf-contract-snapshot"}
    )
    label_artifact_id, _ = await _register_artifact(
        sessions, store, {"fixture": "wp06-perf-final-label", "winner": "YES"}
    )
    signer = Account.from_key("0x" + "17" * 32)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    engine = create_engine(sync_url, poolclass=NullPool)
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO trading.runtime_config_versions "
                "(id,config_key,version_no,content,schema_version,content_hash,status) "
                "VALUES (100,'wp06-perf-config',1,'{}',1,repeat('a',64),'active')"
            ))
            connection.execute(text(
                "INSERT INTO trading.strategy_versions "
                "(id,strategy_key,version_no,content,schema_version,content_hash,status) "
                "VALUES (101,'wp06-perf-strategy',1,'{}',1,repeat('b',64),'active')"
            ))
            connection.execute(text(
                "INSERT INTO trading.execution_spec_versions "
                "(id,spec_key,version_no,content,schema_version,content_hash,status) "
                "VALUES (102,'wp06-perf-execution',1,'{}',1,repeat('c',64),'active')"
            ))
            connection.execute(text(
                "INSERT INTO trading.capital_permission_manifests "
                "(id,name,mode,capability,limits,evaluation_capital,authorized_capital,"
                "kill_switch,content_hash,status) VALUES "
                "(900002,'wp06-perf','shadow','{\"chain_settlement\":"
                "\"FAKE_CONFORMANCE\"}','{}',0,0,false,repeat('d',64),'active')"
            ))
            connection.execute(text(
                "INSERT INTO trading.release_manifests "
                "(id,release_name,config_version_id,strategy_version_id,"
                "execution_spec_version_id,capital_permission_manifest_id,git_sha,"
                "image_digest,db_revision,total_hash,status) VALUES "
                "(900001,'wp06-perf',100,101,102,900002,repeat('e',64),"
                "'fixture:wp06','b1000052',repeat('f',64),'active')"
            ))
            market_id = connection.execute(text(
                "INSERT INTO trading.pm_markets "
                "(gamma_market_id,condition_id,closed,accepting_orders,neg_risk,content_hash) "
                "VALUES ('wp06-perf-market',:condition,true,false,false,:hash) RETURNING id"
            ), {"condition": CONDITION, "hash": "1" * 64}).scalar_one()
            market_version_id = connection.execute(text(
                "INSERT INTO trading.pm_market_versions "
                "(market_id,version_no,resolution_source,closed,accepting_orders,neg_risk,"
                "observed_at,received_at,normalized_hash) VALUES "
                "(:market,1,'fixture',true,false,false,:now,:now,:hash) RETURNING id"
            ), {"market": market_id, "now": now, "hash": "2" * 64}).scalar_one()
            token_yes_id = connection.execute(text(
                "INSERT INTO trading.pm_tokens (token_id,market_id,outcome_index,outcome_label) "
                "VALUES (:token,:market,0,'YES') RETURNING id"
            ), {"token": TOKEN_YES, "market": market_id}).scalar_one()
            token_no_id = connection.execute(text(
                "INSERT INTO trading.pm_tokens (token_id,market_id,outcome_index,outcome_label) "
                "VALUES (:token,:market,1,'NO') RETURNING id"
            ), {"token": TOKEN_NO, "market": market_id}).scalar_one()
            token_yes_version = connection.execute(text(
                "INSERT INTO trading.pm_token_versions "
                "(token_id,version_no,outcome_index,outcome_label,observed_at,received_at) "
                "VALUES (:token,1,0,'YES',:now,:now) RETURNING id"
            ), {"token": token_yes_id, "now": now}).scalar_one()
            token_no_version = connection.execute(text(
                "INSERT INTO trading.pm_token_versions "
                "(token_id,version_no,outcome_index,outcome_label,observed_at,received_at) "
                "VALUES (:token,1,1,'NO',:now,:now) RETURNING id"
            ), {"token": token_no_id, "now": now}).scalar_one()
            snapshot_id = connection.execute(text(
                "INSERT INTO trading.contract_snapshots "
                "(market_version_id,yes_token_version_id,no_token_version_id,"
                "artifact_object_id,resolution_source,content_hash) VALUES "
                "(:market_version,:yes_version,:no_version,:artifact,'fixture',:hash) "
                "RETURNING id"
            ), {"market_version": market_version_id, "yes_version": token_yes_version,
                "no_version": token_no_version, "artifact": snapshot_artifact_id,
                "hash": "3" * 64}).scalar_one()
            contract_spec_id = connection.execute(text(
                "INSERT INTO trading.contract_specs "
                "(contract_key,version_no,snapshot_id,kc_resolution_states,token_ids,"
                "token_count,state_count,compiler_version,schema_version,status,content_hash) "
                "VALUES ('wp06-perf-contract',1,:snapshot,'[\"YES\",\"NO\"]',"
                "CAST(:tokens AS jsonb),2,2,'fixture/v1',1,'pass',:hash) RETURNING id"
            ), {"snapshot": snapshot_id,
                "tokens": json.dumps({"0": token_yes_id, "1": token_no_id}),
                "hash": "4" * 64}).scalar_one()
            connection.execute(text(
                "INSERT INTO trading.payout_functions "
                "(contract_spec_id,pm_token_id,token_version_id,outcome_index,function_ir,"
                "test_vectors,algorithm_hash,content_hash) VALUES "
                "(:spec,:yes,:yes_version,0,'{\"YES\":\"1\",\"NO\":\"0\"}',"
                "'{}',repeat('6',64),repeat('7',64)),"
                "(:spec,:no,:no_version,1,'{\"YES\":\"0\",\"NO\":\"1\"}',"
                "'{}',repeat('8',64),repeat('9',64))"
            ), {"spec": contract_spec_id, "yes": token_yes_id, "no": token_no_id,
                "yes_version": token_yes_version, "no_version": token_no_version})
            connection.execute(text(
                "INSERT INTO trading.resolution_labels "
                "(contract_spec_id,label_key,version_no,state,resolution_state,"
                "resolution_source,evidence_artifact_id,raw_outcome,token_cashflow,policy_code_hash,"
                "auditor_identity) VALUES "
                "(:spec,'wp06-perf-label',1,'final_admissible','YES','fixture',"
                ":artifact,'{}',CAST(:cashflow AS jsonb),:hash,'wp06-perf-auditor')"
            ), {"spec": contract_spec_id, "artifact": label_artifact_id,
                "cashflow": json.dumps({str(token_yes_id): "1", str(token_no_id): "0"}),
                "hash": "5" * 64})

            for kind, address in REGISTRY_ADDRESSES.items():
                connection.execute(text(
                    "SELECT trading.v2_publish_contract_registry("
                    ":version,:kind,1,137,:address,'none',:runtime,NULL,:runtime,"
                    ":snapshot,:block_hash,'fixture://wp06-perf',:retrieved,:content,NULL)"
                ), {"version": "polygon-mainnet-v1", "kind": kind, "address": address,
                    "runtime": CODE_HASH, "snapshot": SNAPSHOT_NUMBER,
                    "block_hash": SNAPSHOT_HASH, "retrieved": now,
                    "content": REGISTRY_HASHES[kind]})

            # Accounts and positions are upstream execution facts.  Every logical
            # operation owns a distinct account/namespace, preserving the production
            # active-redeem and position uniqueness invariants.
            for start in range(0, accounts, 500):
                end = min(accounts, start + 500)
                rows = []
                for index in range(start, end):
                    account_id = index + 1
                    wallet = "0x" + f"{account_id:040x}"
                    rows.append({
                        "id": account_id, "key": f"wp06-perf-account-{account_id}",
                        "wallet": wallet, "signer": signer.address,
                        "namespace": f"exec-{account_id}",
                    })
                connection.execute(text(
                    "INSERT INTO trading.pm_accounts "
                    "(id,account_key,provider,chain_id,identity_type,funder_address,"
                    "maker_address,signing_identity,wallet_type,signature_type,"
                    "release_manifest_id,capital_permission_manifest_id,network_mode,status) "
                    "VALUES (:id,:key,'polymarket',137,'FIXTURE_ONLY',:wallet,:wallet,:signer,"
                    "'deposit_wallet','3',900001,900002,'fixture','active')"
                ), rows)
                connection.execute(text(
                    "INSERT INTO trading.execution_leases "
                    "(account_id,lease_role,owner,lease_until,fencing_token) "
                    "VALUES (:id,'EXECUTION',:owner,:until,1)"
                ), [{"id": row["id"], "owner": RUNTIME_IDENTITY,
                     "until": now + timedelta(hours=3)} for row in rows])
                connection.execute(text(
                    "INSERT INTO trading.positions "
                    "(portfolio_namespace,contract_spec_id,token_id,market_id,quantity,"
                    "cost_basis,account_id) VALUES "
                    "(:namespace,:spec,:token,:market,1,1,:id)"
                ), [{**row, "spec": contract_spec_id, "token": token_yes_id,
                     "market": market_id} for row in rows])
    finally:
        engine.dispose()
    return {
        "market_id": int(market_id), "contract_spec_id": int(contract_spec_id),
        "token_yes_id": int(token_yes_id), "token_no_id": int(token_no_id),
        "signer": signer, "cutoff": now,
    }


async def _record_settlement_sources(
    runtime: EvaluationRuntime,
    sessions: async_sessionmaker,
    store: ArtifactStore,
    seed: dict[str, Any],
) -> str:
    artifacts: dict[str, SettlementSourceArtifact] = {}
    kinds = (
        "gamma_clob_closed", "ctf_payout", "data_api_redeemable",
        "clob_winner_5050", "label_audit",
    )
    cutoff = seed["cutoff"]
    for kind in kinds:
        artifact_id, artifact_hash = await _register_artifact(
            sessions, store, {"fixture": "wp06-perf-source", "kind": kind}
        )
        artifacts[kind] = SettlementSourceArtifact(
            artifact_ref=artifact_hash,
            artifact_hash=artifact_hash,
            artifact_id=artifact_id,
            source_version=f"{kind}/fixture-v1",
            source_cutoff=cutoff,
        )
    engine = runtime._sessions.kw["bind"] if hasattr(runtime._sessions, "kw") else None
    del engine  # Evidence authority is fetched by the runtime; no chain SQL here.
    async with UnitOfWork(sessions) as uow:
        label = (await uow.session.execute(text(
            "SELECT id,version_no,resolution_state FROM trading.resolution_labels "
            "WHERE contract_spec_id=:spec ORDER BY id DESC LIMIT 1"
        ), {"spec": seed["contract"]})).one()
    evidence = ChainSettlementEvidenceInput(
        market_id=seed["market"], condition_id=CONDITION,
        token_set=[TOKEN_YES, TOKEN_NO], cutoff_at=cutoff, received_at=cutoff,
        gamma_closed=True, gamma_accepting_orders=False,
        ctf_outcome_index="YES", ctf_numerator="1", ctf_denominator="1",
        ctf_payout_numerators=["1", "0"], data_api_redeemable=True,
        clob_winner="YES", clob_is_50_50=False,
        label_id=int(label.id), label_version_no=int(label.version_no),
        label_resolution_state=str(label.resolution_state), artifacts=artifacts,
    )
    return await runtime.record_chain_settlement_evidence(evidence)


def _expand_real_position_lineage(
    sync_url: str, seed: dict[str, Any], signer: Any, *, accounts: int
) -> None:
    """Clone the already proven WP-05 authority into account-bound fixture fills.

    The first account/position is created by ``seed_real_position_lineage`` through
    production decision Logic.  Further rows retain that immutable intent authority
    and pass every normal deferred envelope/order/position lineage guard.
    """
    if accounts <= 1:
        return
    engine = create_engine(sync_url, poolclass=NullPool)
    try:
        with engine.begin() as connection:
            intent = connection.execute(text(
                "SELECT i.id,i.intent_hash,d.release_manifest_id,"
                "d.execution_spec_version_id,d.capital_permission_manifest_id "
                "FROM trading.economic_action_intents i JOIN trading.trade_decisions d "
                "ON d.id=i.trade_decision_id JOIN trading.execution_authorization_envelopes e "
                "ON e.intent_id=i.id ORDER BY i.id DESC LIMIT 1"
            )).mappings().one()
            release = connection.execute(text(
                "SELECT release_manifest_id,capital_permission_manifest_id "
                "FROM trading.pm_accounts WHERE id=:account"
            ), {"account": seed["account"]}).mappings().one()
            yes_token_id = int(seed["tokens"][0])
            now = datetime.now(timezone.utc)
            for start in range(2, accounts + 1, 200):
                end = min(accounts + 1, start + 200)
                rows = []
                for account_id in range(start, end):
                    wallet = "0x" + f"{account_id:040x}"
                    rows.append({"id": account_id, "wallet": wallet})
                connection.execute(text(
                    "INSERT INTO trading.pm_accounts "
                    "(id,account_key,provider,chain_id,identity_type,funder_address,"
                    "maker_address,signing_identity,wallet_type,signature_type,"
                    "release_manifest_id,capital_permission_manifest_id,network_mode,status) "
                    "VALUES (:id,'wp06-perf-account-'||:id,'polymarket',137,'FIXTURE_ONLY',"
                    ":wallet,:wallet,:signer,'deposit_wallet','3',:release,:permission,"
                    "'fixture','active')"
                ), [{**row, "signer": signer.address,
                     "release": intent["release_manifest_id"],
                     "permission": intent["capital_permission_manifest_id"]} for row in rows])
                connection.execute(text(
                    "INSERT INTO trading.execution_leases "
                    "(account_id,lease_role,owner,lease_until,fencing_token) "
                    "VALUES (:id,'EXECUTION',:owner,:until,1)"
                ), [{"id": row["id"], "owner": RUNTIME_IDENTITY,
                     "until": now + timedelta(hours=3)} for row in rows])

                envelopes = []
                for row in rows:
                    index = row["id"]
                    envelope_id = connection.execute(text(
                        "INSERT INTO trading.execution_authorization_envelopes "
                        "(envelope_key,intent_id,account_id,release_manifest_id,"
                        "execution_spec_version_id,capital_permission_manifest_id,authority,"
                        "idempotency_key,fencing_token,intent_hash,preflight_hash1,preflight_hash2,"
                        "envelope_hash) VALUES (:key,:intent,:account,:release,:spec,:permission,"
                        "'FAKE_CONFORMANCE',:idem,1,:intent_hash,:p1,:p2,:hash) RETURNING id"
                    ), {"key": f"wp06-perf-envelope-{index}", "intent": intent["id"],
                        "account": index, "release": intent["release_manifest_id"],
                        "spec": intent["execution_spec_version_id"],
                        "permission": intent["capital_permission_manifest_id"],
                        "idem": f"wp06-perf-envelope-idem-{index}",
                        "intent_hash": intent["intent_hash"],
                        "p1": hashlib.sha256(f"p1:{index}".encode()).hexdigest(),
                        "p2": hashlib.sha256(f"p2:{index}".encode()).hexdigest(),
                        "hash": hashlib.sha256(f"envelope:{index}".encode()).hexdigest(),
                    }).scalar_one()
                    envelopes.append((index, int(envelope_id)))
                for index, envelope_id in envelopes:
                    order_id = connection.execute(text(
                        "INSERT INTO trading.exchange_orders "
                        "(order_key,account_id,token_id,side,price,size) "
                        "VALUES (:key,:account,:token,'BUY',0.5,100) RETURNING id"
                    ), {"key": f"wp06-perf-order-{index}", "account": index,
                        "token": TOKEN_YES}).scalar_one()
                    event_id = connection.execute(text(
                        "INSERT INTO trading.order_state_events "
                        "(event_key,order_id,event_type,transition_from,transition_to,event_payload,"
                        "event_hash,fence_token) VALUES (:key,:order,'SUBMITTED','INTENT',"
                        "'SUBMITTED','{}',:hash,1) RETURNING id"
                    ), {"key": f"wp06-perf-submitted-{index}", "order": order_id,
                        "hash": hashlib.sha256(f"event:{index}".encode()).hexdigest(),
                    }).scalar_one()
                    attempt_id = connection.execute(text(
                        "INSERT INTO trading.exchange_order_attempts "
                        "(attempt_key,envelope_id,attempt_no,body_hash,expected_order_hash,"
                        "sdk_manifest_hash,salt,timestamp,fencing_token,result,state_event_id) "
                        "VALUES (:key,:envelope,1,:body,:expected,:sdk,:salt,1,1,'SUBMITTED',"
                        ":event) RETURNING id"
                    ), {"key": f"wp06-perf-attempt-{index}", "envelope": envelope_id,
                        "body": hashlib.sha256(f"body:{index}".encode()).hexdigest(),
                        "expected": hashlib.sha256(f"order:{index}".encode()).hexdigest(),
                        "sdk": hashlib.sha256(f"sdk:{index}".encode()).hexdigest(),
                        "salt": index, "event": event_id}).scalar_one()
                    connection.execute(text(
                        "UPDATE trading.exchange_orders SET attempt_id=:attempt WHERE id=:order"
                    ), {"attempt": attempt_id, "order": order_id})
                    connection.execute(text(
                        "INSERT INTO trading.positions "
                        "(portfolio_namespace,contract_spec_id,token_id,market_id,quantity,"
                        "cost_basis,account_id,envelope_id,order_id) VALUES "
                        "(:namespace,:contract,:token,:market,100,30,:account,:envelope,:order)"
                    ), {"namespace": f"exec-{index}", "contract": seed["contract"],
                        "token": yes_token_id, "market": seed["market"], "account": index,
                        "envelope": envelope_id, "order": order_id})
        with engine.begin() as connection:
            connection.execute(text(
                "UPDATE trading.pm_accounts SET release_manifest_id=:release,"
                "capital_permission_manifest_id=:permission WHERE id BETWEEN 2 AND :last"
            ), {"release": release["release_manifest_id"],
                "permission": release["capital_permission_manifest_id"], "last": accounts})
    finally:
        engine.dispose()


class FakeChain:
    """Deterministic provider state shared by the three fixture drivers."""

    def __init__(self, signer: Any, finalize_accounts: int, polygon_golden: dict) -> None:
        self.signer = signer
        self.finalize_accounts = finalize_accounts
        self.polygon_golden = polygon_golden
        self.tx_to_account: dict[str, int] = {}
        self.tx_to_hash: dict[str, str | None] = {}
        self.tx_to_wallet: dict[str, str] = {}
        self.final_wallets: set[str] = set()
        self.submit_calls = 0
        self.status_calls = 0
        self.tx_probe_failures = 0

    def outside_tx(self) -> None:
        if _TX_DEPTH.get() != 0:
            self.tx_probe_failures += 1
            raise AssertionError("provider_call_inside_db_transaction")

    @staticmethod
    def _rpc_response(payload: dict[str, Any], result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": payload["id"], "result": result}

    @staticmethod
    def _normalized(value: Any) -> Any:
        if isinstance(value, str) and value.startswith("0x"):
            return value.lower()
        if isinstance(value, list):
            return [FakeChain._normalized(item) for item in value]
        if isinstance(value, dict):
            return {key: FakeChain._normalized(item) for key, item in value.items()}
        return value

    def _golden_result(self, method: str, params: list[Any]) -> Any:
        for key, request in self.polygon_golden["requests"].items():
            if request["method"] == method and self._normalized(
                request["params"]
            ) == self._normalized(params):
                node = self.polygon_golden["rpc_nodes"][0]
                return self.polygon_golden["responses"][key][node]["result"]
        raise AssertionError(f"unfrozen polygon request: {method} {params}")

    async def polygon(self, payload: dict[str, Any], _endpoint: str) -> dict[str, Any]:
        self.outside_tx()
        method = payload["method"]
        params = payload["params"]
        if method == "eth_getBlockByNumber":
            tag = params[0]
            if tag == hex(RECEIPT_NUMBER):
                number, block_hash = RECEIPT_NUMBER, RECEIPT_HASH
            elif tag == "finalized":
                number, block_hash = FINALIZED_NUMBER, FINALIZED_HASH
            else:
                return self._rpc_response(payload, self._golden_result(method, params))
            return self._rpc_response(payload, {"number": hex(number), "hash": block_hash,
                "parentHash": "0x" + "ee" * 32, "timestamp": hex(1_780_000_000)})
        if method == "eth_getTransactionReceipt":
            tx_hash = str(params[0]).lower()
            return self._rpc_response(payload, {
                "transactionHash": tx_hash, "status": "0x1",
                "blockNumber": hex(RECEIPT_NUMBER), "blockHash": RECEIPT_HASH,
                "transactionIndex": "0x0", "logs": [],
            })
        if method == "eth_call":
            calldata = str(params[0]["data"]).lower()
            selector = calldata[:10]
            if selector == "0xe985e9c5":
                value = 1
            elif selector == "0x70a08231":
                wallet = "0x" + calldata[34:74]
                value = 1100 if wallet in self.final_wallets else 1000
            elif selector == "0x00fdd58e":
                wallet = "0x" + calldata[34:74]
                token = int(calldata[74:138], 16)
                value = 0 if wallet in self.final_wallets else (
                    100 if token == int(TOKEN_YES) else 0
                )
            else:
                return self._rpc_response(payload, self._golden_result(method, params))
            return self._rpc_response(payload, "0x" + value.to_bytes(32, "big").hex())
        return self._rpc_response(payload, self._golden_result(method, params))

    async def relayer(
        self, method: str, path: str, *, params: dict[str, str],
        body: bytes | None, headers: dict[str, str],
    ) -> tuple[int, bytes]:
        del headers
        self.outside_tx()
        if path == "/v1/account/transactions/params":
            return 200, json.dumps({
                "address": params["address"], "nonce": "7"
            }, separators=(",", ":")).encode()
        if method == "POST" and path == "/submit":
            self.submit_calls += 1
            parsed = json.loads(body or b"{}")
            wallet = str(parsed["depositWalletParams"]["depositWallet"]).lower()
            account_id = self.submit_calls
            tx_id = f"wp06-perf-tx-{account_id}"
            tx_hash = "0x" + hashlib.sha256(tx_id.encode()).hexdigest()
            final = account_id <= self.finalize_accounts
            self.tx_to_account[tx_id] = account_id
            self.tx_to_hash[tx_id] = tx_hash if final else None
            self.tx_to_wallet[tx_id] = wallet
            response: dict[str, Any] = {
                "transactionID": tx_id, "transactionHash": tx_hash if final else None,
                # CONFIRMED is intentionally not an accepted immediate-submit state:
                # TX2 persists UNKNOWN, forcing the read-only recovery path.
                "state": "STATE_CONFIRMED",
            }
            return 200, json.dumps(response, separators=(",", ":")).encode()
        if method == "GET" and path.startswith("/v1/account/transactions/"):
            self.status_calls += 1
            tx_id = path.rsplit("/", 1)[-1]
            account_id = self.tx_to_account[tx_id]
            tx_hash = self.tx_to_hash[tx_id]
            if tx_hash:
                self.final_wallets.add(self.tx_to_wallet[tx_id])
            return 200, json.dumps({
                "transaction_id": tx_id, "transaction_hash": tx_hash,
                "state": "STATE_CONFIRMED" if tx_hash else "STATE_NEW",
                "error_msg": None,
            }, separators=(",", ":")).encode()
        raise AssertionError(f"unexpected relayer request {method} {path}")


def _instrument_engine(async_engine: Any) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "pool_wait_ms": [], "checked_out": 0, "connection_high_water": 0,
    }
    pool = async_engine.sync_engine.pool
    original_do_get = pool._do_get

    def timed_do_get() -> Any:
        started = time.perf_counter()
        try:
            return original_do_get()
        finally:
            metrics["pool_wait_ms"].append((time.perf_counter() - started) * 1000)

    pool._do_get = timed_do_get

    @event.listens_for(async_engine.sync_engine, "begin")
    def on_begin(_connection: Any) -> None:
        _TX_DEPTH.set(_TX_DEPTH.get() + 1)

    def on_end(_connection: Any) -> None:
        _TX_DEPTH.set(max(0, _TX_DEPTH.get() - 1))

    event.listen(async_engine.sync_engine, "commit", on_end)
    event.listen(async_engine.sync_engine, "rollback", on_end)

    @event.listens_for(pool, "checkout")
    def on_checkout(*_args: Any) -> None:
        metrics["checked_out"] += 1
        metrics["connection_high_water"] = max(
            metrics["connection_high_water"], metrics["checked_out"]
        )

    @event.listens_for(pool, "checkin")
    def on_checkin(*_args: Any) -> None:
        metrics["checked_out"] -= 1

    return metrics


def _instrument_stages(runtime: EvaluationRuntime) -> dict[str, list[float]]:
    stages: dict[str, list[float]] = defaultdict(list)

    def wrap(obj: Any, name: str, stage: str) -> None:
        original: Callable[..., Awaitable[Any]] = getattr(obj, name)

        async def measured(*args: Any, **kwargs: Any) -> Any:
            started = time.perf_counter()
            try:
                return await original(*args, **kwargs)
            finally:
                stages[stage].append((time.perf_counter() - started) * 1000)

        setattr(obj, name, measured)

    wrap(runtime._chain_logic, "preflight_redeem", "registry_preflight")
    wrap(runtime._chain_logic, "prepare_redeem", "tx1")
    wrap(runtime._relayer, "submit_prepared", "fake_submit")
    wrap(runtime._chain_logic, "apply_submit_outcome", "tx2")
    wrap(runtime._chain_logic, "apply_recovery", "receipt_finalized_apply")
    return stages


async def _snapshot_operations(
    sessions: async_sessionmaker, account_start: int, account_end: int
) -> tuple[str, list[tuple[Any, ...]]]:
    async with UnitOfWork(sessions) as uow:
        rows = (await uow.session.execute(text(
            "SELECT account_id,operation_key,status,expected_operation_hash,"
            "economic_hash,body_hash,economic_effect_applied FROM trading.chain_operations "
            "WHERE account_id BETWEEN :start AND :end ORDER BY account_id"
        ), {"start": account_start, "end": account_end})).all()
    material = [list(row) for row in rows]
    return canonical_hash(material), rows


async def _validate_db(
    sessions: async_sessionmaker, *, expected_ops: int, finalized_ops: int
) -> dict[str, int]:
    queries = {
        "operations": "SELECT count(*) FROM trading.chain_operations",
        "duplicate_operations": (
            "SELECT count(*) FROM (SELECT operation_key FROM trading.chain_operations "
            "GROUP BY operation_key HAVING count(*)>1) d"
        ),
        "finalized": "SELECT count(*) FROM trading.chain_operations WHERE status='FINALIZED'",
        "lost_effect": (
            "SELECT count(*) FROM trading.chain_operations WHERE status='FINALIZED' "
            "AND economic_effect_applied IS NOT TRUE"
        ),
        "duplicate_effect": (
            "SELECT count(*) FROM (SELECT chain_operation_id,portfolio_namespace "
            "FROM trading.ledger_transactions WHERE kind='SETTLEMENT' "
            "GROUP BY 1,2 HAVING count(*)>1) d"
        ),
        "unbalanced_assets": (
            "SELECT count(*) FROM (SELECT p.transaction_id,p.asset_type,p.asset_key,"
            "sum(p.amount) total FROM trading.ledger_postings p "
            "JOIN trading.ledger_transactions t ON t.id=p.transaction_id "
            "WHERE t.kind='SETTLEMENT' GROUP BY 1,2,3 HAVING sum(p.amount)<>0) d"
        ),
        "nonzero_finalized_positions": (
            "SELECT count(*) FROM trading.positions p JOIN trading.chain_operations o "
            "ON o.account_id=p.account_id WHERE o.status='FINALIZED' AND p.quantity<>0"
        ),
        "outbox": (
            "SELECT count(*) FROM trading.transactional_outbox "
            "WHERE topic='chain.settlement.finalized'"
        ),
        "conflicts": (
            "SELECT count(*) FROM trading.chain_operations "
            "WHERE status='SETTLEMENT_CONFLICT'"
        ),
    }
    result: dict[str, int] = {}
    async with UnitOfWork(sessions) as uow:
        for name, sql in queries.items():
            result[name] = int((await uow.session.execute(text(sql))).scalar_one())
    result["expected_operations"] = expected_ops
    result["expected_finalized"] = finalized_ops
    return result


async def _run_workload(
    runtime: EvaluationRuntime,
    sessions: async_sessionmaker,
    fake: FakeChain,
    *,
    market_id: int,
    duration_s: float,
    g1_capacity: int,
    g2_unknown: int,
) -> dict[str, Any]:
    g1_ids: list[int] = []
    g1_recovery_ms: list[float] = []
    counter = 0
    lock = asyncio.Lock()
    workload_started = time.perf_counter()
    deadline = workload_started + duration_s
    target_rate = 12.0 if duration_s < 10 else 11.0

    async def next_account() -> int | None:
        nonlocal counter
        async with lock:
            if counter >= g1_capacity or time.perf_counter() >= deadline:
                return None
            counter += 1
            account_id = counter
        # A fixed issue schedule proves that the measured rate is sustained for the
        # whole interval instead of completing a burst and sleeping afterwards.
        scheduled_at = workload_started + (account_id - 1) / target_rate
        await asyncio.sleep(max(0.0, scheduled_at - time.perf_counter()))
        return account_id

    async def worker() -> None:
        while True:
            account_id = await next_account()
            if account_id is None:
                return
            request = ChainRedeemRequest(
                operation_key=f"wp06-perf-g1-{account_id}",
                idempotency_key=f"wp06-perf-g1-idem-{account_id}",
                account_id=account_id, market_id=market_id,
                condition_id=CONDITION, fencing_token=1,
            )
            submitted = await runtime.submit_redeem(request)
            if submitted["status"] != "UNKNOWN":
                raise AssertionError(f"submit did not converge to UNKNOWN: {submitted}")
            started = time.perf_counter()
            recovered = await runtime.recover_chain_operation(
                operation_id=int(submitted["operation_id"]), fencing_token=1
            )
            g1_recovery_ms.append((time.perf_counter() - started) * 1000)
            if recovered["status"] != "FINALIZED" or recovered.get("applied") is not True:
                raise AssertionError(f"canonical finality not applied: {recovered}")
            g1_ids.append(int(submitted["operation_id"]))

    started = workload_started
    await asyncio.gather(*(worker() for _ in range(12)))
    # The final scheduled issue is just before the boundary.  Hold the meter to
    # the complete contractual interval instead of reporting a 59.99s sample.
    await asyncio.sleep(max(0.0, deadline - time.perf_counter()))
    elapsed = time.perf_counter() - started
    ops_per_second = len(g1_ids) / elapsed

    # G2 uses the same submit business path but the deterministic status response is
    # NEW with no transaction hash.  Recovery is read-only and idempotent.
    g2_start = g1_capacity + 1
    g2_ids: list[int] = []
    # Admission control is deliberately aligned with the configured 5+1 pool.
    # Queueing belongs at the logical-work boundary, not inside QueuePool where
    # it would inflate connection acquisition latency and starve recovery work.
    semaphore = asyncio.Semaphore(6)

    async def make_unknown(offset: int) -> None:
        account_id = g2_start + offset
        async with semaphore:
            submitted = await runtime.submit_redeem(ChainRedeemRequest(
                operation_key=f"wp06-perf-g2-{offset}",
                idempotency_key=f"wp06-perf-g2-idem-{offset}",
                account_id=account_id, market_id=market_id,
                condition_id=CONDITION, fencing_token=1,
            ))
        if submitted["status"] != "UNKNOWN":
            raise AssertionError(f"G2 seed not UNKNOWN: {submitted}")
        g2_ids.append(int(submitted["operation_id"]))

    await asyncio.gather(*(make_unknown(index) for index in range(g2_unknown)))
    submit_calls_before_recovery = fake.submit_calls
    recovery_ms: list[float] = []

    async def recover_one(operation_id: int) -> None:
        async with semaphore:
            started_at = time.perf_counter()
            await runtime.recover_chain_operation(
                operation_id=operation_id, fencing_token=1
            )
            recovery_ms.append((time.perf_counter() - started_at) * 1000)

    first_started = time.perf_counter()
    await asyncio.gather(*(recover_one(operation_id) for operation_id in g2_ids))
    first_elapsed = time.perf_counter() - first_started
    first_hash, first_rows = await _snapshot_operations(
        sessions, g2_start, g2_start + g2_unknown - 1
    )
    second_started = time.perf_counter()
    await asyncio.gather(*(recover_one(operation_id) for operation_id in g2_ids))
    second_elapsed = time.perf_counter() - second_started
    second_hash, second_rows = await _snapshot_operations(
        sessions, g2_start, g2_start + g2_unknown - 1
    )
    return {
        "g1": {
            "ops": len(g1_ids), "elapsed_s": elapsed,
            "ops_per_second": ops_per_second, "recovery_ms": g1_recovery_ms,
        },
        "g2": {
            "ops": len(g2_ids), "pass1_s": first_elapsed, "pass2_s": second_elapsed,
            "recovery_ms": recovery_ms, "first_hash": first_hash,
            "second_hash": second_hash, "identical": first_rows == second_rows,
            "blind_resend": fake.submit_calls - submit_calls_before_recovery,
        },
    }


async def _async_main(
    sync_url: str, async_url: str, artifact_root: str, report: dict[str, Any]
) -> None:
    quick = os.environ.get("PM_V2_PERF_QUICK") == "1"
    duration_s = 2.0 if quick else 60.0
    g1_capacity = 24 if quick else 660
    g2_unknown = 20 if quick else 1_000
    total_accounts = g1_capacity + g2_unknown
    report["plan"] = {
        "mode": "quick" if quick else "contract", "duration_s": duration_s,
        "g1_capacity": g1_capacity, "g2_unknown": g2_unknown,
        "execution_pool": {"pool_size": 5, "max_overflow": 1},
    }

    async_engine = create_async_engine(async_url, pool_size=5, max_overflow=1)
    sessions = async_sessionmaker(async_engine, expire_on_commit=False)
    pool_metrics = _instrument_engine(async_engine)
    store = ArtifactStore(
        LocalArtifactDriver(artifact_root),
        Settings(_env_file=None, ARTIFACT_LOCAL_ROOT=artifact_root),
    )
    from tests.trading.fixtures.p6_settlement.p6_helpers import frozen_fixture
    from tests.trading.integration.wp06_runtime_fixture import (
        SIGNER as FIXTURE_SIGNER,
        seed_authority as seed_runtime_authority,
        seed_real_position_lineage,
    )

    seed = seed_runtime_authority(sync_url)
    seed = await seed_real_position_lineage(sessions, sync_url, seed)
    seed["cutoff"] = datetime.now(timezone.utc).replace(microsecond=0)
    _expand_real_position_lineage(
        sync_url, seed, FIXTURE_SIGNER, accounts=total_accounts
    )
    fake = FakeChain(
        FIXTURE_SIGNER, finalize_accounts=g1_capacity,
        polygon_golden=frozen_fixture("polygon_rpc_golden"),
    )

    @fixture_polygon_transport
    async def polygon_transport(payload: dict[str, Any], endpoint: str) -> dict[str, Any]:
        return await fake.polygon(payload, endpoint)

    @fixture_relayer_transport
    async def relayer_transport(
        method: str, path: str, *, params: dict[str, str],
        body: bytes | None, headers: dict[str, str],
    ) -> tuple[int, bytes]:
        return await fake.relayer(
            method, path, params=params, body=body, headers=headers
        )

    polygon = PolygonDriver(
        rpc_urls=("https://fixture-a.invalid", "https://fixture-b.invalid", "https://fixture-c.invalid"),
        transport=polygon_transport, fixture_only=True,
    )
    relayer = RelayerDriver(
        transport=relayer_transport, fixture_only=True,
        trusted_time_provider=lambda: 1_780_000_000,
        signer=lambda message: "0x" + FIXTURE_SIGNER.sign_message(message).signature.hex(),
        nonce_auth_provider=lambda address: {
            "RELAYER_API_KEY": "fixture-key", "RELAYER_API_KEY_ADDRESS": address,
        },
        builder_auth_provider=lambda timestamp, _method, _path, _body: {
            "POLY_BUILDER_API_KEY": "fixture-builder",
            "POLY_BUILDER_TIMESTAMP": str(timestamp),
            "POLY_BUILDER_PASSPHRASE": "fixture-passphrase",
            "POLY_BUILDER_SIGNATURE": "fixture-signature",
        },
    )
    geo_now = lambda: datetime.now(timezone.utc).replace(microsecond=0)

    @fixture_geoblock_transport
    async def geo_transport() -> dict[str, Any]:
        fake.outside_tx()
        return {
            "allowed": True, "observed_at": geo_now().isoformat(),
            "source_version": "wp06-perf-geoblock/v1", "region_code": "CA",
        }

    geoblock = GeoblockDriver(
        transport=geo_transport, now_provider=geo_now, fixture_only=True
    )
    runtime = EvaluationRuntime(
        sessions, store, polygon_driver=polygon, relayer_driver=relayer,
        geoblock_driver=geoblock, runtime_identity=RUNTIME_IDENTITY,
        registry_content_hashes=REGISTRY_HASHES,
    )
    settlement_set_key = await _record_settlement_sources(runtime, sessions, store, seed)
    stage_metrics = _instrument_stages(runtime)

    async with UnitOfWork(sessions) as uow:
        # Keep the sample as numeric bytes.  asyncpg decodes ``pg_lsn`` to its
        # integer representation, which cannot safely be rebound as a pg_lsn.
        wal_start = int((await uow.session.execute(text(
            "SELECT pg_wal_lsn_diff(pg_current_wal_lsn(),'0/0')"
        ))).scalar_one())
    cpu_start = time.process_time()
    rss_start = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    loop_lag: list[float] = []
    stop_lag = asyncio.Event()

    async def measure_loop_lag() -> None:
        while not stop_lag.is_set():
            started = time.perf_counter()
            await asyncio.sleep(0.01)
            loop_lag.append(max(0.0, (time.perf_counter() - started - 0.01) * 1000))

    lag_task = asyncio.create_task(measure_loop_lag())
    try:
        workload = await _run_workload(
            runtime, sessions, fake, market_id=seed["market"],
            duration_s=duration_s, g1_capacity=g1_capacity, g2_unknown=g2_unknown,
        )
    finally:
        stop_lag.set()
        await lag_task
    async with UnitOfWork(sessions) as uow:
        wal_end = int((await uow.session.execute(text(
            "SELECT pg_wal_lsn_diff(pg_current_wal_lsn(),'0/0')"
        ))).scalar_one())
        wal_bytes = wal_end - wal_start
    counters = await _validate_db(
        sessions,
        expected_ops=workload["g1"]["ops"] + g2_unknown,
        finalized_ops=workload["g1"]["ops"],
    )
    fake_calls = polygon.fake_calls + relayer.fake_calls + geoblock.transport_calls
    real_calls = polygon.real_calls + relayer.real_calls
    pool_p95 = _pct(pool_metrics["pool_wait_ms"], 0.95)
    g1_rate = workload["g1"]["ops_per_second"]
    g1_scale_ok = workload["g1"]["ops"] > 0 if quick else workload["g1"]["elapsed_s"] >= 60
    gates = {
        "g1_throughput": {
            "pass": g1_scale_ok and g1_rate >= 10,
            "ops": workload["g1"]["ops"],
            "elapsed_s": round(workload["g1"]["elapsed_s"], 3),
            "ops_per_second": round(g1_rate, 3), "threshold": 10,
        },
        "g2_unknown_recovery": {
            "pass": workload["g2"]["ops"] == g2_unknown
                    and workload["g2"]["identical"]
                    and workload["g2"]["blind_resend"] == 0,
            **{key: value for key, value in workload["g2"].items()
               if key not in {"recovery_ms"}},
        },
        "g3_pool": {
            "pass": pool_p95 <= 20 and pool_metrics["connection_high_water"] <= 6,
            "wait_p95_ms": round(pool_p95, 3), "wait_threshold_ms": 20,
            "connection_high_water": pool_metrics["connection_high_water"],
            "configured_total": 6,
        },
        "correctness": {
            "pass": counters["operations"] == counters["expected_operations"]
                    and counters["finalized"] == counters["expected_finalized"]
                    and counters["outbox"] == counters["expected_finalized"]
                    and all(counters[name] == 0 for name in (
                        "duplicate_operations", "lost_effect", "duplicate_effect",
                        "unbalanced_assets", "nonzero_finalized_positions", "conflicts",
                    )),
            "lost": counters["lost_effect"],
            "duplicate": counters["duplicate_operations"] + counters["duplicate_effect"],
            "unbalanced": counters["unbalanced_assets"],
            "conflict": counters["conflicts"],
        },
        "provider_boundary": {
            "pass": fake_calls > 0 and real_calls == 0 and fake.tx_probe_failures == 0,
            "fake_calls": fake_calls, "real_calls": real_calls,
            "transaction_probe_failures": fake.tx_probe_failures,
        },
    }
    report.update({
        "seed": {
            "database_revision": "b1000052", "settlement_set_key": settlement_set_key,
            "condition_id": CONDITION, "registry_hashes": REGISTRY_HASHES,
            "fixture_hash": canonical_hash({
                "condition": CONDITION, "tokens": [TOKEN_YES, TOKEN_NO],
                "registry": REGISTRY_HASHES,
            }),
        },
        "gates": gates,
        "latencies_ms": {
            **{name: _summary(values) for name, values in stage_metrics.items()},
            "logical_recovery_finality": _summary(workload["g1"]["recovery_ms"]),
            "recovery_unknown_1000": _summary(workload["g2"]["recovery_ms"]),
            "pool_wait": _summary(pool_metrics["pool_wait_ms"]),
            "event_loop_lag": _summary(loop_lag),
        },
        "counters": {
            **counters, "fake_transport_calls": fake_calls,
            "real_network_chain_money_calls": real_calls,
            "relayer_submit_calls": fake.submit_calls,
            "relayer_status_calls": fake.status_calls,
            "wal_bytes": wal_bytes,
            "cpu_seconds": round(time.process_time() - cpu_start, 3),
            "rss_start_kib": rss_start,
            "rss_peak_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        },
    })
    report["hard_assertions"] = (
        "PASS" if all(gate["pass"] for gate in gates.values()) else "FAIL"
    )
    await async_engine.dispose()


def main() -> int:
    report: dict[str, Any] = {
        "task": "WP-06 §9 chain settlement vertical smoke",
        "git": _git_state(), "hard_assertions": "FAIL",
        "cleanup": {"database_removed": False, "artifact_root_removed": False},
    }
    sync_url = ""
    artifact_tmp = tempfile.mkdtemp(prefix="pm_v2_perf6_artifacts_")
    error: BaseException | None = None
    try:
        sync_url, async_url = _create_database()
        _upgrade(sync_url)
        asyncio.run(_async_main(sync_url, async_url, artifact_tmp, report))
    except BaseException as exc:  # report remains useful on a hard assertion/crash
        error = exc
        report["error"] = {"type": type(exc).__name__, "message": str(exc)}
        report["hard_assertions"] = "FAIL"
    finally:
        if sync_url:
            try:
                report["cleanup"]["database_removed"] = _drop_database(sync_url)
            except BaseException as cleanup_exc:
                report["cleanup"]["database_error"] = str(cleanup_exc)
        shutil.rmtree(artifact_tmp, ignore_errors=True)
        report["cleanup"]["artifact_root_removed"] = not Path(artifact_tmp).exists()
        if not all(report["cleanup"].get(key) is True for key in (
            "database_removed", "artifact_root_removed"
        )):
            report["hard_assertions"] = "FAIL"
        REPORT_PATH.write_text(
            json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n"
        )
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return 1 if error is not None or report["hard_assertions"] != "PASS" else 0


if __name__ == "__main__":
    raise SystemExit(main())
