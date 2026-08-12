"""Shared production-path WP-06 PostgreSQL fixture (no replica role)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from eth_account import Account
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.uow import UnitOfWork
from app.domain.trading.hashing import canonical_bytes, canonical_hash
from app.repositories.trading.market_stream import MarketStreamRepository
from app.schemas.trading.settlement import (
    ChainRedeemRequest,
    ChainSettlementEvidenceInput,
    SettlementSourceArtifact,
    SETTLEMENT_SOURCE_KINDS,
)
from app.services.artifact_store import ArtifactStore
from app.services.artifact_store.drivers.local import LocalArtifactDriver
from app.services.polymarket.geoblock_driver import (
    GeoblockDriver,
    fixture_geoblock_transport,
)
from app.services.polymarket.polygon_driver import (
    PolygonDriver,
    fixture_polygon_transport,
)
from app.services.polymarket.relayer_driver import (
    RelayerDriver,
    fixture_relayer_transport,
)
from runtimes.trading.evaluation import EvaluationRuntime
from tests.trading.fixtures.p6_settlement.p6_helpers import frozen_fixture, relayer_golden

SERVE_DIR = Path(__file__).resolve().parents[3]
ALEMBIC_DIR = SERVE_DIR / "alembic"
RUNTIME_IDENTITY = "wp06-runtime"
CONDITION = "0x" + "77" * 32
YES_TOKEN = "1000001"
NO_TOKEN = "1000002"
PK = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
SIGNER = Account.from_key(PK)
WALLET = relayer_golden()["submit"]["body"]["depositWalletParams"]["depositWallet"]
URLS = ("https://rpc-a.example", "https://rpc-b.example", "https://rpc-c.example")


def upgrade(url: str) -> None:
    cfg = Config()
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    engine = create_engine(url, poolclass=NullPool)
    conn = engine.connect()
    cfg.attributes["connection"] = conn
    try:
        command.upgrade(cfg, "b1000052")
    finally:
        conn.close(); engine.dispose()


def async_url(url: str) -> str:
    return url.replace("postgresql+psycopg:///", "postgresql+asyncpg:///")


def query(url: str, sql: str, params: dict | None = None):
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.connect() as conn:
            return conn.execute(text(sql), params or {}).mappings().all()
    finally:
        engine.dispose()


def seed_authority(url: str) -> dict[str, Any]:
    registry = frozen_fixture("contract_registry")
    entries = {
        row["name"]: row for row in registry["entries"]
        if row["name"] in {"pusd", "ctf", "deposit_wallet", "ctf_adapter_standard"}
    }
    now = datetime.now(timezone.utc)
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.begin() as c:
            cfg = c.execute(text(
                "INSERT INTO trading.runtime_config_versions(config_key,version_no,content,"
                "schema_version,content_hash,status) VALUES('wp06-cfg',1,'{}',1,:h,'active') RETURNING id"
            ), {"h": "a" * 64}).scalar_one()
            strat = c.execute(text(
                "INSERT INTO trading.strategy_versions(strategy_key,version_no,content,"
                "schema_version,content_hash,status) VALUES('wp06-strat',1,'{}',1,:h,'active') RETURNING id"
            ), {"h": "b" * 64}).scalar_one()
            spec_version = c.execute(text(
                "INSERT INTO trading.execution_spec_versions(spec_key,version_no,content,"
                "schema_version,content_hash,status) VALUES('wp06-exec',1,'{}',1,:h,'active') RETURNING id"
            ), {"h": "c" * 64}).scalar_one()
            permission = c.execute(text(
                "INSERT INTO trading.capital_permission_manifests(name,mode,capability,limits,"
                "evaluation_capital,authorized_capital,kill_switch,content_hash,status) "
                "VALUES('wp06-permission','shadow',CAST(:cap AS jsonb),'{}',0,0,false,:h,'active') RETURNING id"
            ), {"cap": json.dumps({"chain_settlement": "FAKE_CONFORMANCE"}), "h": "d" * 64}).scalar_one()
            release = c.execute(text(
                "INSERT INTO trading.release_manifests(release_name,config_version_id,strategy_version_id,"
                "execution_spec_version_id,capital_permission_manifest_id,git_sha,image_digest,db_revision,"
                "total_hash,status) VALUES('wp06-release',:cfg,:strat,:exec,:perm,:git,'fixture-image',"
                "'b1000052',:h,'active') RETURNING id"
            ), {"cfg": cfg, "strat": strat, "exec": spec_version, "perm": permission,
                "git": "e" * 64, "h": "f" * 64}).scalar_one()
            account = c.execute(text(
                "INSERT INTO trading.pm_accounts(account_key,provider,chain_id,identity_type,funder_address,"
                "maker_address,signing_identity,wallet_type,signature_type,release_manifest_id,"
                "capital_permission_manifest_id,network_mode,status) VALUES('wp06-account','polymarket',137,"
                "'FIXTURE_ONLY',:wallet,:wallet,:signer,'deposit_wallet','3',:release,:permission,'fixture',"
                "'active') RETURNING id"
            ), {"wallet": WALLET, "signer": SIGNER.address, "release": release,
                "permission": permission}).scalar_one()
            c.execute(text(
                "INSERT INTO trading.execution_leases(account_id,lease_role,owner,lease_until,fencing_token) "
                "VALUES(:account,'EXECUTION',:owner,:until,1)"
            ), {"account": account, "owner": RUNTIME_IDENTITY, "until": now + timedelta(hours=1)})
            # This authority-only market provides the initial account/release FK graph.
            # The production decision fixture below owns the actual settlement market;
            # keep these natural identifiers distinct so no surrogate-id rewrite is
            # hidden by disabled triggers.
            market = c.execute(text(
                "INSERT INTO trading.pm_markets(gamma_market_id,condition_id,closed,accepting_orders,"
                "active,enable_order_book,neg_risk,content_hash) VALUES('wp06-market',:condition,true,false,"
                "false,true,false,:hash) RETURNING id"
            ), {"condition": "0x" + "66" * 32, "hash": "1" * 64}).scalar_one()
            market_version = c.execute(text(
                "INSERT INTO trading.pm_market_versions(market_id,version_no,resolution_source,closed,"
                "accepting_orders,neg_risk,observed_at,received_at,normalized_hash) "
                "VALUES(:market,1,'gamma',true,false,false,:at,:at,:hash) RETURNING id"
            ), {"market": market, "at": now, "hash": "2" * 64}).scalar_one()
            token_ids = []
            token_versions = []
            for index, (external, label) in enumerate((("9000001", "YES"), ("9000002", "NO"))):
                token = c.execute(text(
                    "INSERT INTO trading.pm_tokens(token_id,market_id,outcome_index,outcome_label) "
                    "VALUES(:token,:market,:idx,:label) RETURNING id"
                ), {"token": external, "market": market, "idx": index, "label": label}).scalar_one()
                version = c.execute(text(
                    "INSERT INTO trading.pm_token_versions(token_id,version_no,outcome_index,outcome_label,"
                    "observed_at,received_at) VALUES(:token,1,:idx,:label,:at,:at) RETURNING id"
                ), {"token": token, "idx": index, "label": label, "at": now}).scalar_one()
                token_ids.append(token); token_versions.append(version)
            static_hash = hashlib.sha256(b"wp06-static-evidence").hexdigest()
            artifact = c.execute(text(
                "INSERT INTO trading.artifact_objects(sha256,original_size,stored_size,mime,compression,"
                "storage_driver,storage_version,locator) VALUES(:h,20,20,'application/json','none','local',"
                "'cas/v1',:loc) RETURNING id"
            ), {"h": static_hash, "loc": f"cas/v1/sha256/{static_hash[:2]}/{static_hash[2:4]}/{static_hash}.raw"}).scalar_one()
            snapshot = c.execute(text(
                "INSERT INTO trading.contract_snapshots(market_version_id,yes_token_version_id,"
                "no_token_version_id,artifact_object_id,resolution_source,content_hash) "
                "VALUES(:mv,:yes,:no,:artifact,'gamma',:hash) RETURNING id"
            ), {"mv": market_version, "yes": token_versions[0], "no": token_versions[1],
                "artifact": artifact, "hash": "3" * 64}).scalar_one()
            contract = c.execute(text(
                "INSERT INTO trading.contract_specs(contract_key,version_no,snapshot_id,kc_resolution_states,"
                "token_ids,token_count,state_count,compiler_version,schema_version,status,content_hash) "
                "VALUES('wp06-contract',1,:snapshot,CAST(:states AS jsonb),CAST(:tokens AS jsonb),2,2,"
                "'wp06/v1',1,'pass',:hash) RETURNING id"
            ), {"snapshot": snapshot,
                "tokens": json.dumps({"0": str(token_ids[0]), "1": str(token_ids[1])}), "hash": "4" * 64,
                "states": json.dumps(["YES", "NO"])}).scalar_one()
            for index, token in enumerate(token_ids):
                ir = {"YES": "1" if index == 0 else "0",
                      "NO": "0" if index == 0 else "1"}
                c.execute(text(
                    "INSERT INTO trading.payout_functions(contract_spec_id,pm_token_id,token_version_id,"
                    "outcome_index,function_ir,test_vectors,algorithm_hash,content_hash) VALUES(:contract,"
                    ":token,:version,:idx,CAST(:ir AS jsonb),'{}',:algorithm,:hash)"
                ), {"contract": contract, "token": token, "version": token_versions[index],
                    "idx": index, "ir": json.dumps(ir), "algorithm": "5" * 64,
                    "hash": f"{6 + index}" * 64})
            label = c.execute(text(
                "INSERT INTO trading.resolution_labels(contract_spec_id,label_key,version_no,state,"
                "policy_code_hash) VALUES(:contract,'wp06-label',1,'pending',:policy) RETURNING id"
            ), {"contract": contract, "policy": "8" * 64}).scalar_one()
            for kind, row in entries.items():
                c.execute(text(
                    "INSERT INTO trading.contract_registry(registry_version,kind,version_no,chain_id,address,"
                    "proxy_kind,runtime_keccak,resolved_implementation_or_beacon,resolved_code_keccak,"
                    "snapshot_block_number,snapshot_block_hash,source_url,retrieved_at,content_hash,extra,status) "
                    "VALUES(:registry,:kind,:version,137,:address,:proxy,:runtime,:resolved,:resolved_hash,"
                    ":number,:block_hash,:source,:retrieved,:content,CAST(:extra AS jsonb),'ACTIVE')"
                ), {"registry": row["registry_version"], "kind": kind, "version": row.get("version_no", 1),
                    "address": row["address"], "proxy": row["proxy_kind"],
                    "runtime": row["runtime_keccak"], "resolved": row.get("resolved_implementation_or_beacon"),
                    "resolved_hash": row["resolved_code_keccak"], "number": row["snapshot_block_number"],
                    "block_hash": row["snapshot_block_hash"], "source": row["source_url"],
                    "retrieved": datetime.fromisoformat(row["retrieved_at"]), "content": row["hash"],
                    "extra": json.dumps(row.get("extra") or {})})
    finally:
        engine.dispose()
    return {"account": account, "market": market, "label": label, "contract": contract,
            "tokens": token_ids, "entries": entries, "permission": permission,
            "release": release}


async def seed_real_position_lineage(sessions, url: str, ids: dict[str, Any]) -> dict[str, Any]:
    """Create an account position through the full WP-03/05 authority lineage.

    The decision episode/action/intent uses production Logic. The static provider fill
    lineage (envelope/order/submitted event/attempt) is inserted under normal trigger
    enforcement; no replica role or disabled trigger is used.
    """
    from app.logics.trading.decision import DecisionLogic
    from app.repositories.trading.cohort import CohortRepository
    from app.repositories.trading.decision import DecisionRepository
    from app.repositories.trading.execution import ExecutionRepository
    from app.repositories.trading.forecast import ForecastRepository
    from app.repositories.trading.ledger import LedgerRepository
    from app.repositories.trading.semantics import SemanticsRepository
    from app.repositories.trading.workflow import WorkflowRepository
    from app.schemas.trading.decision import (
        ActionCandidateInput, ActionSetInput, MarketRelativeInput,
        PortfolioGateInput, UnderwritingInput,
    )
    from tests.trading.integration.test_v2_decision_shadow_workflow import (
        _build_blind_committed_episode, _quote_map, _seed,
    )

    env = {
        "sessions": sessions,
        "decision": DecisionRepository(), "execution": ExecutionRepository(),
        "ledger": LedgerRepository(), "forecast": ForecastRepository(),
        "wf": WorkflowRepository(), "cohort": CohortRepository(),
        "sem": SemanticsRepository(),
    }
    ctx = await _seed(env)
    episode, spec_ids = await _build_blind_committed_episode(env, ctx)
    logic = DecisionLogic(env["decision"], env["wf"])
    async with UnitOfWork(sessions) as uow:
        checkpoint_at = (await uow.session.execute(text(
            "SELECT received_at FROM trading.pm_book_checkpoints WHERE token_id='token-p2-yes'"
        ))).scalar_one()
    trigger_at = checkpoint_at + timedelta(minutes=1)
    async with UnitOfWork(sessions) as uow:
        created = await logic.create_decision(
            uow, episode_id=episode, trigger_at=trigger_at, experiment_variant="champion"
        )
    assert created.ok, created.reason
    async with UnitOfWork(sessions) as uow:
        revealed = await logic.reveal(
            uow, trade_decision_id=created.trade_decision_id,
            quote_reveal_at=trigger_at + timedelta(seconds=1), quotes=_quote_map(ctx),
        )
    assert revealed.ok, revealed.reason
    async with UnitOfWork(sessions) as uow:
        await uow.session.execute(text(
            "INSERT INTO trading.operating_cost_entries(cost_key,cost_kind,amount,"
            "release_manifest_id,episode_id,trade_decision_id,allocation_policy) VALUES"
            "(:key,'INFRASTRUCTURE',0,:release,:episode,:decision,CAST(:policy AS jsonb))"
        ), {"key": f"wp06-cost-{created.trade_decision_id}", "release": ctx["release"],
            "episode": episode, "decision": created.trade_decision_id,
            "policy": json.dumps({"kind": "fixed_marginal", "evidence": "observed_zero"})})
    async with UnitOfWork(sessions) as uow:
        result = await logic.market_relative(
            uow, trade_decision_id=created.trade_decision_id,
            input_=MarketRelativeInput(decision_mode="BLIND_ONLY"),
        )
    assert result.ok, result.reason
    contract = spec_ids[0]
    async with UnitOfWork(sessions) as uow:
        result = await logic.run_g7a(
            uow, trade_decision_id=created.trade_decision_id,
            candidates=[ActionCandidateInput(
                contract_spec_id=contract, token_id=ctx["yes_token"],
                action_type="BUY_TOKEN", target_quantity=100,
            )], policy_hash=None, version_manifest_id=None,
        )
    assert result.ok, result.reason
    async with UnitOfWork(sessions) as uow:
        result = await logic.run_g7b(
            uow, trade_decision_id=created.trade_decision_id,
            portfolio=PortfolioGateInput(), policy_hash=None, version_manifest_id=None,
        )
    assert result.ok, result.reason
    async with UnitOfWork(sessions) as uow:
        result = await logic.terminalize(
            uow, trade_decision_id=created.trade_decision_id,
            action_set=ActionSetInput(
                disposition="ACTION", selected_action_type="BUY_TOKEN",
                legs={"open": {contract: {ctx["yes_token"]: 100}}},
            ),
            underwriting=UnderwritingInput(
                plan_version=1, entry_range={"min": "0.50", "max": "0.55"},
                hold_to_resolution=True, thesis_hash="a" * 64,
                invalidation={"evidence": "fixture"},
            ), decided_at=trigger_at + timedelta(seconds=2),
        )
    assert result.ok, result.reason

    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.begin() as c:
            intent = c.execute(text(
                "SELECT id,intent_hash FROM trading.economic_action_intents "
                "WHERE trade_decision_id=:decision"
            ), {"decision": created.trade_decision_id}).mappings().one()
            # The immutable P2 release remains the fill-lineage authority.  The
            # account temporarily points at it while the deferred envelope guard
            # runs; after this transaction commits we restore the separately
            # frozen WP-06 chain release rather than mutating either control fact.
            c.execute(text(
                "UPDATE trading.pm_accounts SET release_manifest_id=:release,"
                "capital_permission_manifest_id=:permission WHERE id=:account"
            ), {"release": ctx["release"], "permission": ctx["capital"],
                "account": ids["account"]})
            # Convert the already-proven P2 market to the closed settlement cut.
            c.execute(text(
                "UPDATE trading.pm_markets SET condition_id=:condition,closed=true,"
                "accepting_orders=false,active=false,neg_risk=false,content_hash=:hash "
                "WHERE id=:market"
            ), {"condition": CONDITION, "hash": "9" * 64, "market": ctx["market"]})
            c.execute(text(
                "UPDATE trading.pm_tokens SET token_id=CASE outcome_index WHEN 0 THEN :yes ELSE :no END "
                "WHERE market_id=:market"
            ), {"yes": YES_TOKEN, "no": NO_TOKEN, "market": ctx["market"]})
            artifact_id = c.execute(text(
                "SELECT artifact_object_id FROM trading.contract_snapshots cs "
                "JOIN trading.contract_specs spec ON spec.snapshot_id=cs.id WHERE spec.id=:contract"
            ), {"contract": contract}).scalar_one()
            label_pending = c.execute(text(
                "INSERT INTO trading.resolution_labels(contract_spec_id,label_key,version_no,state,"
                "policy_code_hash) VALUES(:contract,'wp06-final',1,'pending',:policy) RETURNING id"
            ), {"contract": contract, "policy": "9" * 64}).scalar_one()
            label_provisional = c.execute(text(
                "INSERT INTO trading.resolution_labels(contract_spec_id,label_key,version_no,state,"
                "policy_code_hash,supersedes_id) VALUES(:contract,'wp06-final',2,'provisional',:policy,"
                ":supersedes) RETURNING id"
            ), {"contract": contract, "policy": "a" * 64,
                "supersedes": label_pending}).scalar_one()
            label = c.execute(text(
                "INSERT INTO trading.resolution_labels(contract_spec_id,label_key,version_no,state,"
                "resolution_state,resolution_source,evidence_artifact_id,raw_outcome,token_cashflow,"
                "policy_code_hash,supersedes_id,auditor_identity) VALUES(:contract,'wp06-final',3,"
                "'final_admissible','YES','gamma',:artifact,'{}',CAST(:cashflow AS jsonb),:policy,"
                ":supersedes,'wp06-auditor') RETURNING id"
            ), {"contract": contract, "artifact": artifact_id,
                "cashflow": json.dumps({str(ctx["yes_token"]): "1", str(ctx["no_token"]): "0"}),
                "policy": "b" * 64, "supersedes": label_provisional}).scalar_one()
            envelope = c.execute(text(
                "INSERT INTO trading.execution_authorization_envelopes(envelope_key,intent_id,account_id,"
                "release_manifest_id,execution_spec_version_id,capital_permission_manifest_id,authority,"
                "idempotency_key,fencing_token,intent_hash,preflight_hash1,preflight_hash2,envelope_hash) "
                "VALUES('wp06-envelope',:intent,:account,:release,:spec,:permission,'FAKE_CONFORMANCE',"
                "'wp06-envelope-idem',1,:intent_hash,:p1,:p2,:hash) RETURNING id"
            ), {"intent": intent["id"], "account": ids["account"], "release": ctx["release"],
                "spec": ctx["exec_spec"], "permission": ctx["capital"],
                "intent_hash": intent["intent_hash"], "p1": "c" * 64, "p2": "d" * 64,
                "hash": "e" * 64}).scalar_one()
            order = c.execute(text(
                "INSERT INTO trading.exchange_orders(order_key,account_id,token_id,side,price,size) "
                "VALUES('wp06-order',:account,:token,'BUY',0.5,100) RETURNING id"
            ), {"account": ids["account"], "token": YES_TOKEN}).scalar_one()
            event = c.execute(text(
                "INSERT INTO trading.order_state_events(event_key,order_id,event_type,transition_from,"
                "transition_to,event_payload,event_hash,fence_token) VALUES('wp06-submitted',:order,"
                "'SUBMITTED','INTENT','SUBMITTED','{}',:hash,1) RETURNING id"
            ), {"order": order, "hash": "f" * 64}).scalar_one()
            attempt = c.execute(text(
                "INSERT INTO trading.exchange_order_attempts(attempt_key,envelope_id,attempt_no,body_hash,"
                "expected_order_hash,sdk_manifest_hash,salt,timestamp,fencing_token,result,state_event_id) "
                "VALUES('wp06-attempt',:envelope,1,:body,:expected,:sdk,1,1,1,'SUBMITTED',:event) RETURNING id"
            ), {"envelope": envelope, "body": "1" * 64, "expected": "2" * 64,
                "sdk": "3" * 64, "event": event}).scalar_one()
            c.execute(text(
                "UPDATE trading.exchange_orders SET attempt_id=:attempt WHERE id=:order"
            ), {"attempt": attempt, "order": order})
            c.execute(text(
                "INSERT INTO trading.positions(portfolio_namespace,contract_spec_id,token_id,market_id,"
                "quantity,cost_basis,account_id,envelope_id,order_id) VALUES(:namespace,:contract,:token,"
                ":market,100,30,:account,:envelope,:order)"
            ), {"namespace": f"exec-{ids['account']}", "contract": contract,
                "token": ctx["yes_token"], "market": ctx["market"], "account": ids["account"],
                "envelope": envelope, "order": order})
        with engine.begin() as c:
            c.execute(text(
                "UPDATE trading.pm_accounts SET release_manifest_id=:release,"
                "capital_permission_manifest_id=:permission WHERE id=:account"
            ), {"release": ids["release"], "permission": ids["permission"],
                "account": ids["account"]})
    finally:
        engine.dispose()
    return {**ids, "market": ctx["market"], "label": label, "contract": contract,
            "tokens": [ctx["yes_token"], ctx["no_token"]]}


async def seed_settlement(
    runtime: EvaluationRuntime,
    store: ArtifactStore,
    ids: dict[str, Any],
    *,
    clob_winner: str = "YES",
) -> str:
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=1)
    received = datetime.now(timezone.utc)
    refs = {}
    for kind in SETTLEMENT_SOURCE_KINDS:
        refs[kind] = store.put_bytes(canonical_bytes({"kind": kind, "cutoff": cutoff}), "application/json", "none")
    artifacts = {}
    async with UnitOfWork(runtime._sessions) as uow:
        catalog = MarketStreamRepository()
        for kind, ref in refs.items():
            artifact_id = await catalog.register_artifact(uow.session, ref)
            artifacts[kind] = SettlementSourceArtifact(
                artifact_ref=ref.sha256, artifact_hash=ref.sha256, artifact_id=artifact_id,
                source_version=f"{kind}/fixture-v1", source_cutoff=cutoff,
            )
    return await runtime.record_chain_settlement_evidence(ChainSettlementEvidenceInput(
        market_id=ids["market"], condition_id=CONDITION, token_set=[YES_TOKEN, NO_TOKEN],
        cutoff_at=cutoff, received_at=received, gamma_closed=True, gamma_accepting_orders=False,
        ctf_outcome_index="YES", ctf_numerator="1", ctf_denominator="1",
        ctf_payout_numerators=["1", "0"], data_api_redeemable=True,
        clob_winner=clob_winner, clob_is_50_50=False,
        label_id=ids["label"], label_version_no=3,
        label_resolution_state="YES", artifacts=artifacts,
    ))


def build_runtime(url: str, root: Path, ids: dict[str, Any]):
    state = {"submitted": False, "submit_calls": 0, "transport_in_tx": [],
             "status_calls": 0, "enforce_no_tx": True}
    async_engine = create_async_engine(async_url(url), pool_size=4, max_overflow=0)

    def assert_network_outside_transaction(boundary: str) -> None:
        checked_out = async_engine.sync_engine.pool.checkedout()
        state["transport_in_tx"].append((boundary, checked_out))
        if state["enforce_no_tx"]:
            assert checked_out == 0, f"network boundary {boundary} held {checked_out} DB connections"
    golden = frozen_fixture("polygon_rpc_golden")
    node_by_url = dict(zip(URLS, golden["rpc_nodes"], strict=True))

    def norm(value):
        if isinstance(value, str) and value.startswith("0x"):
            return value.lower()
        if isinstance(value, list): return [norm(item) for item in value]
        if isinstance(value, dict): return {key: norm(item) for key, item in value.items()}
        return value

    def golden_result(method: str, params: list, endpoint: str):
        for key, request in golden["requests"].items():
            if request["method"] == method and norm(request["params"]) == norm(params):
                return golden["responses"][key][node_by_url[endpoint]]["result"]
        raise AssertionError(f"unfrozen RPC request: {method} {params}")

    @fixture_polygon_transport
    async def polygon_transport(payload, endpoint):
        assert_network_outside_transaction("polygon")
        method, params = payload["method"], payload["params"]
        if method == "eth_getBlockByNumber" and params[0] == "finalized" and not state["submitted"]:
            result = {"number": "0x5796656", "hash": "0x" + "ab" * 32,
                      "parentHash": "0x" + "aa" * 32, "timestamp": "0x6a7b4000", "transactions": []}
        elif method == "eth_call" and params[0]["data"].startswith(("0x70a08231", "0x00fdd58e", "0xe985e9c5")):
            data = params[0]["data"]
            if data.startswith("0xe985e9c5"):
                value = 1
            elif data.startswith("0x70a08231"):
                value = 1100 if state["submitted"] else 1000
            else:
                token = int(data[-64:], 16)
                value = 0 if state["submitted"] else (100 if token == int(YES_TOKEN) else 0)
            result = "0x" + value.to_bytes(32, "big").hex()
        else:
            result = golden_result(method, params, endpoint)
        return {"jsonrpc": "2.0", "id": payload["id"], "result": result}

    g = relayer_golden()
    secret = hashlib.sha256(b"pm-v2/fixture/builder-secret/v1").digest()

    @fixture_relayer_transport
    async def relayer_transport(method, path, *, params=None, body=None, headers=None):
        assert_network_outside_transaction(f"relayer:{path}")
        if path == "/v1/account/transactions/params":
            response = g["nonce"]["response"]
        elif path == "/submit":
            state["submitted"] = True; state["submit_calls"] += 1
            response = g["submit"]["response"]
        elif path.startswith("/v1/account/transactions/"):
            state["status_calls"] += 1; response = g["status"]["response"]
        else:
            return 404, b"{}"
        return 200, json.dumps(response, separators=(",", ":")).encode()

    def nonce_auth(address):
        return {"RELAYER_API_KEY": "fixture-key", "RELAYER_API_KEY_ADDRESS": address}

    def builder_auth(timestamp, method, path, body):
        message = f"{timestamp}{method.upper()}{path}{body.decode()}".encode()
        signature = base64.urlsafe_b64encode(hmac.new(secret, message, hashlib.sha256).digest()).decode()
        return {"POLY_BUILDER_API_KEY": "fixture-builder", "POLY_BUILDER_TIMESTAMP": str(timestamp),
                "POLY_BUILDER_PASSPHRASE": "fixture-passphrase", "POLY_BUILDER_SIGNATURE": signature}

    @fixture_geoblock_transport
    async def geo_transport():
        assert_network_outside_transaction("geoblock")
        return {"allowed": True, "observed_at": datetime.now(timezone.utc).isoformat(),
                "region_code": "CA", "source_version": "fixture-geoblock/v1"}

    sessions = async_sessionmaker(async_engine, expire_on_commit=False)
    store = ArtifactStore(LocalArtifactDriver(str(root)))
    polygon = PolygonDriver(rpc_urls=URLS, transport=polygon_transport)
    relayer = RelayerDriver(transport=relayer_transport,
        trusted_time_provider=lambda: g["deadline"]["trusted_now"],
        signer=lambda signable: "0x" + SIGNER.sign_message(signable).signature.hex(),
        nonce_auth_provider=nonce_auth, builder_auth_provider=builder_auth)
    geo = GeoblockDriver(transport=geo_transport)
    hashes = {kind: row["hash"] for kind, row in ids["entries"].items()}
    runtime = EvaluationRuntime(sessions, store, polygon_driver=polygon, relayer_driver=relayer,
        geoblock_driver=geo, runtime_identity=RUNTIME_IDENTITY, registry_content_hashes=hashes)
    return runtime, store, state, async_engine


def request(ids: dict[str, Any], suffix: str = "one") -> ChainRedeemRequest:
    return ChainRedeemRequest(operation_key=f"wp06-redeem-{suffix}", idempotency_key=f"wp06-idem-{suffix}",
        account_id=ids["account"], market_id=ids["market"], condition_id=CONDITION, fencing_token=1)
