"""
WP-05 vault / accounts / funds / reservations —— 真 PostgreSQL 集成（Checkpoint B）。

覆盖：account/funds/snapshot 链、vault store/read/rotate 真实 DB roundtrip、
access event 追加、reservation 状态机（DB trigger 强制）、funds 恒等式 DB CHECK、
单 ACTIVE deferred trigger（并发激活被拒）。
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.logics.trading.portfolio import PortfolioLogic
from app.repositories.trading.execution import ExecutionRepository
from app.repositories.trading.vault import VaultRepository
from app.services.vault import VaultAuthError, VaultService

SERVE_DIR = __import__("pathlib").Path(__file__).resolve().parents[3]
ALEMBIC_DIR = SERVE_DIR / "alembic"
HEAD = "b1000050"

K1 = os.urandom(32)
K2 = os.urandom(32)
KEYRING = {("k1", "v1"): K1, ("k1", "v2"): K2}


def _canonical_hash(obj) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def seed_intent_chain(session, chain) -> dict:
    """在 ``session_replication_role = replica`` 下种子最小 economic_action_intents 链。

    决策触发链（episode/trade_decision/action_set 的 deferred complete 校验）对 reservation
    测试无关；沿用 0031 迁移测试的 replica 模式绕过用户触发，FK 目标仍真实落库。
    返回 {intent_id, decision_id, action_set_id}。
    """
    await session.execute(text("SET LOCAL session_replication_role = replica"))
    h = "a" * 64
    wsv = (await session.execute(text(
        "INSERT INTO trading.world_schema_versions "
        "(component_id, version_no, variables, domains, constraints, factorization, "
        " world_states, state_count, resolution_map, h_c, content_hash, schema_version) "
        "VALUES (0, 1, '{}'::jsonb, '{}'::jsonb, '[]'::jsonb, '{}'::jsonb, "
        " '[\"w0\"]'::jsonb, 1, '{}'::jsonb, '{}'::jsonb, :h, 1) RETURNING id"),
        {"h": h})).scalar_one()
    comp = (await session.execute(text(
        "INSERT INTO trading.forecast_components (component_key) "
        "VALUES ('comp-res') RETURNING id"))).scalar_one()
    fcv = (await session.execute(text(
        "INSERT INTO trading.forecast_component_versions "
        "(component_id, version_no, world_schema_version_id, content_hash) "
        "VALUES (:c, 1, :w, :h) RETURNING id"),
        {"c": comp, "w": wsv, "h": h})).scalar_one()
    cohort = (await session.execute(text(
        "INSERT INTO trading.evaluation_cohorts "
        "(cohort_key, objective_contract_id, strategy_version_id, release_manifest_id, "
        " policy_hashes, seed_hash) "
        "VALUES ('cohort-res', :obj, :strat, :rel, '{}'::jsonb, :h) RETURNING id"),
        {"obj": chain["objective_id"], "strat": chain["strategy_id"],
         "rel": chain["release_manifest_id"], "h": h})).scalar_one()
    opp = (await session.execute(text(
        "INSERT INTO trading.decision_opportunities "
        "(opportunity_key, cohort_id, chain_type, objective_contract_id, strategy_version_id, "
        " status, triggered_at) "
        "VALUES ('opp-res', :cohort, 'DECISION', :obj, :strat, 'OPEN', now()) RETURNING id"),
        {"cohort": cohort, "obj": chain["objective_id"], "strat": chain["strategy_id"]})).scalar_one()
    episode = (await session.execute(text(
        "INSERT INTO trading.forecast_episodes "
        "(episode_key, decision_opportunity_id, component_version_id, strategy_version_id, "
        " objective_contract_id, trigger, cutoff_at, horizon, experiment_variant) "
        "VALUES (:h, :opp, :fcv, :strat, :obj, 'G7', now()+interval '1 day', "
        " 'HOLD_TO_RESOLUTION', 'champion') RETURNING id"),
        {"h": h, "opp": opp, "fcv": fcv, "strat": chain["strategy_id"],
         "obj": chain["objective_id"]})).scalar_one()
    fim = (await session.execute(text(
        "INSERT INTO trading.forecast_input_manifests "
        "(episode_id, manifest_key, manifest_hash, evidence_bundle_hash, contract_spec_set_hash, "
        " world_schema_hash, prior_hash, taxonomy_hash, model_binding_hash, prompt_hash, "
        " code_hash, content) "
        "VALUES (:ep, 'fim-res', :h, :h, :h, :h, :h, :h, :h, :h, :h, '{}'::jsonb) RETURNING id"),
        {"ep": episode, "h": h})).scalar_one()
    sub = (await session.execute(text(
        "INSERT INTO trading.forecast_submissions "
        "(episode_id, submission_key, q, u, forecast_input_manifest_id, "
        " contract_schema_prior_evidence_hash, algorithm_hash) "
        "VALUES (:ep, 'sub-res', '{\"w0\":\"1\"}'::jsonb, '[{\"w0\":\"1\"}]'::jsonb, :fim, :h, :h) RETURNING id"),
        {"ep": episode, "fim": fim, "h": h})).scalar_one()
    lease = (await session.execute(text(
        "INSERT INTO trading.forecast_leases "
        "(submission_id, valid_until, invalidation_conditions, evidence_hash, schema_hash, spec_hash) "
        "VALUES (:sub, now()+interval '1 day', '{}'::jsonb, :h, :h, :h) RETURNING id"),
        {"sub": sub, "h": h})).scalar_one()
    decision = (await session.execute(text(
        "INSERT INTO trading.trade_decisions "
        "(decision_key, episode_id, forecast_submission_id, forecast_lease_id, "
        " objective_contract_id, strategy_version_id, release_manifest_id, "
        " execution_spec_version_id, capital_permission_manifest_id, "
        " experiment_variant, status, selected_action_type, trigger_at, quote_bound_at, "
        " decided_at, input_hash) VALUES "
        "(:h, :ep, :sub, :lease, :obj, :strat, :rel, :exec, :cap, "
        " 'champion', 'ACTION', 'BUY_TOKEN', now()-interval '1 hour', now()-interval '30 minutes', "
        " now(), :h) RETURNING id"),
        {"h": h, "ep": episode, "sub": sub, "lease": lease,
         "obj": chain["objective_id"], "strat": chain["strategy_id"],
         "rel": chain["release_manifest_id"], "exec": chain["execution_spec_version_id"],
         "cap": chain["capital_permission_manifest_id"]})).scalar_one()
    aset = (await session.execute(text(
        "INSERT INTO trading.action_sets "
        "(action_set_key, trade_decision_id, disposition, action_set_hash) "
        "VALUES ('as-res', :d, 'ACTION', :h) RETURNING id"),
        {"d": decision, "h": h})).scalar_one()
    intent = (await session.execute(text(
        "INSERT INTO trading.economic_action_intents "
        "(intent_key, intent_hash, trade_decision_id, action_set_id, preflight) "
        "VALUES ('intent-res', :h, :d, :as, '{}'::jsonb) RETURNING id"),
        {"h": h, "d": decision, "as": aset})).scalar_one()
    await session.execute(text("SET LOCAL session_replication_role = origin"))
    return {"intent_id": intent, "decision_id": decision, "action_set_id": aset}


async def seed_control_chain(session) -> dict:
    """最小 control 链：runtime config / objective / strategy / exec spec / permission / release。"""
    cfg_id = (
        await session.execute(
            text(
                "INSERT INTO trading.runtime_config_versions "
                "(config_key, version_no, content, schema_version, content_hash, status) "
                "VALUES ('runtime/v1', 1, CAST(:c AS jsonb), 1, :h, 'active') RETURNING id"
            ),
            {"c": json.dumps({"k": "v"}), "h": _canonical_hash({"k": "v"})},
        )
    ).scalar_one()
    objective_id = (
        await session.execute(
            text(
                "INSERT INTO trading.strategy_objective_contracts "
                "(contract_key, version_no, content, schema_version, content_hash, status) "
                "VALUES ('objective/v1', 1, CAST(:c AS jsonb), 1, :h, 'active') RETURNING id"
            ),
            {"c": json.dumps({"units": "USD"}), "h": _canonical_hash({"units": "USD"})},
        )
    ).scalar_one()
    strategy_id = (
        await session.execute(
            text(
                "INSERT INTO trading.strategy_versions "
                "(strategy_key, version_no, content, schema_version, content_hash, status) "
                "VALUES ('strategy/v1', 1, CAST(:c AS jsonb), 1, :h, 'active') RETURNING id"
            ),
            {"c": json.dumps({"k": "v"}), "h": _canonical_hash({"k": "v"})},
        )
    ).scalar_one()
    exec_spec_id = (
        await session.execute(
            text(
                "INSERT INTO trading.execution_spec_versions "
                "(spec_key, version_no, content, schema_version, content_hash, status) "
                "VALUES ('exec/v1', 1, CAST(:c AS jsonb), 1, :h, 'active') RETURNING id"
            ),
            {"c": json.dumps({"execution_mode": "shadow_only"}),
             "h": _canonical_hash({"execution_mode": "shadow_only"})},
        )
    ).scalar_one()
    cap_id = (
        await session.execute(
            text(
                "INSERT INTO trading.capital_permission_manifests "
                "(name, mode, capability, limits, evaluation_capital, authorized_capital, "
                " kill_switch, content_hash, status) "
                "VALUES ('shadow-fixture', 'shadow', CAST(:cap AS jsonb), CAST(:lim AS jsonb), "
                " 1000, 0, false, :h, 'active') RETURNING id"
            ),
            {
                "cap": json.dumps({"can_open": True}),
                "lim": json.dumps({}),
                "h": _canonical_hash({"name": "shadow-fixture"}),
            },
        )
    ).scalar_one()
    release_id = (
        await session.execute(
            text(
                "INSERT INTO trading.release_manifests "
                "(release_name, config_version_id, strategy_version_id, execution_spec_version_id, "
                " capital_permission_manifest_id, git_sha, image_digest, db_revision, total_hash, status) "
                "VALUES ('release/v1', :cfg, :strat, :exec, :cap, :git, :img, :db, :h, 'active') RETURNING id"
            ),
            {
                "cfg": cfg_id, "strat": strategy_id, "exec": exec_spec_id, "cap": cap_id,
                "git": "a" * 64, "img": "sha256:b" * 8, "db": HEAD,
                "h": _canonical_hash({"release": "release/v1"}),
            },
        )
    ).scalar_one()
    return {
        "objective_id": objective_id, "strategy_id": strategy_id,
        "execution_spec_version_id": exec_spec_id,
        "capital_permission_manifest_id": cap_id, "release_manifest_id": release_id,
    }


@pytest_asyncio.fixture
async def env(temp_pg_db):
    cfg = Config()
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    engine = create_engine(temp_pg_db.url, poolclass=NullPool)
    conn = engine.connect()
    cfg.attributes["connection"] = conn
    try:
        command.upgrade(cfg, "head")
    finally:
        conn.close()
        engine.dispose()

    async_url = make_url(temp_pg_db.url).set(drivername="postgresql+asyncpg").render_as_string(
        hide_password=False
    )
    async_engine = create_async_engine(async_url, pool_size=2, max_overflow=0)
    sessions = async_sessionmaker(async_engine, expire_on_commit=False)

    async with sessions() as session:
        chain = await seed_control_chain(session)
        await session.commit()

    try:
        yield {
            "sessions": sessions,
            "chain": chain,
            "vault_repo": VaultRepository(),
            "exec_repo": ExecutionRepository(),
        }
    finally:
        await async_engine.dispose()


async def _make_vault_entry(env, *, secret_kind="signer_private_key"):
    svc = VaultService(env["vault_repo"], KEYRING, env="test")
    async with env["sessions"]() as session:
        entry = await svc.create_entry(
            session, name="pm/signer/fixture-1", secret_kind=secret_kind,
            runtime_identity="worker-a",
        )
        await session.commit()
        return entry["id"], svc


async def _make_account(env, *, signer_entry_id=None, l2_entry_id=None) -> int:
    repo = env["exec_repo"]
    chain = env["chain"]
    async with env["sessions"]() as session:
        account = await repo.insert_account(
            session,
            account_key="fixture-acct-1",
            provider="polymarket",
            chain_id=137,
            identity_type="FIXTURE_ONLY",
            funder_address="0x" + "a" * 40,
            maker_address="0x" + "b" * 40,
            signing_identity="0x" + "c" * 40,
            wallet_type="deposit_wallet",
            signature_type="3",
            signer_secret_entry_id=signer_entry_id,
            signer_secret_version_id=1,
            l2_secret_entry_id=l2_entry_id,
            l2_secret_version_id=1,
            release_manifest_id=chain["release_manifest_id"],
            capital_permission_manifest_id=chain["capital_permission_manifest_id"],
            network_mode="fixture",
        )
        await session.commit()
        return account["id"]


async def _create_funds(env, account_id, *, confirmed=100, provider_reserved=10) -> int:
    repo = env["exec_repo"]
    async with env["sessions"]() as session:
        snapshot = await repo.insert_balance_snapshot(
            session, account_id=account_id, asset_key="USD", spender="0x" + "d" * 40,
            balance=confirmed, allowance=confirmed, provider_reserved=provider_reserved,
            observed_at=datetime.now(timezone.utc),
            request_hash="f" * 64, fencing_token=1, completeness="COMPLETE",
        )
        funds = await repo.create_funds(
            session, account_id=account_id, asset_key="USD",
            confirmed=confirmed, provider_reserved=provider_reserved, local_reserved=0,
            available=confirmed - provider_reserved,
            source_snapshot_id=snapshot["id"], reconcile_watermark=1,
        )
        await session.commit()
        return funds["id"]


@pytest.mark.asyncio
async def test_account_funds_snapshot_chain(env):
    entry_id, _ = await _make_vault_entry(env)
    account_id = await _make_account(env, signer_entry_id=entry_id)
    funds_id = await _create_funds(env, account_id)
    assert funds_id is not None
    repo = env["exec_repo"]
    async with env["sessions"]() as session:
        funds = await repo.get_funds(session, account_id=account_id, asset_key="USD")
        assert funds["confirmed"] == Decimal("100")
        assert funds["provider_reserved"] == Decimal("10")
        assert funds["local_reserved"] == Decimal("0")
        assert funds["available"] == Decimal("90")
        account = await repo.get_account(session, account_id=account_id)
        assert account["identity_type"] == "FIXTURE_ONLY"
        assert account["signature_type"] == "3"


@pytest.mark.asyncio
async def test_vault_store_read_rotate_roundtrip(env):
    entry_id, svc = await _make_vault_entry(env)
    async with env["sessions"]() as session:
        v1 = await svc.store_secret(
            session, entry_id=entry_id, secret=b"signer-secret-v1", purpose="sign",
            identity="worker-a", account="fixture-acct-1", key_id="k1", key_version="v1",
        )
        assert (await svc.read_secret(
            session, entry_id=entry_id, version_id=v1["id"], purpose="sign",
            identity="worker-a", account="fixture-acct-1",
        )) == b"signer-secret-v1"
        v2 = await svc.rotate_secret(
            session, entry_id=entry_id, secret=b"signer-secret-v2", purpose="sign",
            identity="worker-a", account="fixture-acct-1", key_id="k1", key_version="v2",
        )
        assert (await svc.read_secret(
            session, entry_id=entry_id, version_id=v2["id"], purpose="sign",
            identity="worker-a", account="fixture-acct-1",
        )) == b"signer-secret-v2"
        # 旧版本仍可读
        assert (await svc.read_secret(
            session, entry_id=entry_id, version_id=v1["id"], purpose="sign",
            identity="worker-a", account="fixture-acct-1",
        )) == b"signer-secret-v1"
        # 单 active
        versions = await env["vault_repo"].list_versions(session, entry_id=entry_id)
        assert sum(1 for v in versions if v["status"] == "active") == 1
        await session.commit()

    # access events 已落库
    async with env["sessions"]() as session:
        events = (
            await session.execute(
                text("SELECT result FROM trading.secret_access_events WHERE entry_id=:e"),
                {"e": entry_id},
            )
        ).scalars().all()
    assert "STORED" in events and "READ" in events and "ROTATED" in events


@pytest.mark.asyncio
async def test_single_active_deferred_trigger(env):
    entry_id, svc = await _make_vault_entry(env)
    async with env["sessions"]() as session:
        v1 = await svc.store_secret(
            session, entry_id=entry_id, secret=b"v1", purpose="sign", identity="worker-a",
            account="fixture-acct-1", key_id="k1", key_version="v1",
        )
        v2 = await svc.rotate_secret(
            session, entry_id=entry_id, secret=b"v2", purpose="sign", identity="worker-a",
            account="fixture-acct-1", key_id="k1", key_version="v2",
        )
        await session.commit()
        old_id = v1["id"]
        # 试图把 retired 旧版本重新激活 → deferred 单 active trigger 拒绝
        with pytest.raises(Exception, match="v2_vault_entry_multiple_active"):
            await env["vault_repo"].activate_version(session, version_id=old_id)
            await session.commit()


@pytest.mark.asyncio
async def test_funds_identity_db_enforced(env):
    entry_id, _ = await _make_vault_entry(env)
    account_id = await _make_account(env, signer_entry_id=entry_id)
    async with env["sessions"]() as session:
        snapshot = await env["exec_repo"].insert_balance_snapshot(
            session, account_id=account_id, asset_key="USD", spender=None,
            balance=100, allowance=100, provider_reserved=0,
            observed_at=datetime.now(timezone.utc),
            request_hash="e" * 64, fencing_token=1, completeness="COMPLETE",
        )
        # 错误恒等式（available <> confirmed - provider - local）→ CHECK violation
        with pytest.raises(Exception, match="ck_account_funds_current_identity|account_funds_current_identity"):
            await env["exec_repo"].create_funds(
                session, account_id=account_id, asset_key="USD", confirmed=100,
                provider_reserved=0, local_reserved=0, available=99,
                source_snapshot_id=snapshot["id"], reconcile_watermark=1,
            )
            await session.commit()
        await session.rollback()


class _UoW:
    """把 AsyncSession 包成 PortfolioLogic 需要的 uow.session 形态。"""

    def __init__(self, session):
        self.session = session


@pytest.mark.asyncio
async def test_reservation_state_machine_via_db(env):
    entry_id, _ = await _make_vault_entry(env)
    account_id = await _make_account(env, signer_entry_id=entry_id)
    await _create_funds(env, account_id, confirmed=100, provider_reserved=0)
    async with env["sessions"]() as session:
        intent = await seed_intent_chain(session, env["chain"])
        await session.commit()
    intent_id = intent["intent_id"]
    logic = PortfolioLogic(execution=env["exec_repo"])

    # Tx A：HELD reserve + commit
    async with env["sessions"]() as session:
        res = await logic.reserve_funds(
            _UoW(session), reservation_key="res-1", intent_id=intent_id, account_id=account_id,
            asset_key="USD", amount=Decimal("40"), idempotency_key="ik-1",
        )
        assert res["status"] == "HELD"
        await session.commit()
        res_id = res["id"]

    # Tx B：HELD → UNKNOWN（保留 local）+ commit
    async with env["sessions"]() as session:
        await logic.mark_reservation_unknown(_UoW(session), reservation_id=res_id)
        await session.commit()

    # Tx C：UNKNOWN 禁止直接 RELEASED（DB BEFORE trigger）；整 tx 回滚
    async with env["sessions"]() as session:
        with pytest.raises(Exception, match="v2_reservation_transition_invalid"):
            await env["exec_repo"].advance_reservation(
                session, reservation_id=res_id, new_status="RELEASED"
            )
            await session.commit()
        await session.rollback()

    # 回滚后仍是 UNKNOWN，local 保留
    async with env["sessions"]() as session:
        res = await env["exec_repo"].get_reservation(session, reservation_id=res_id)
        assert res["status"] == "UNKNOWN"
        funds = await env["exec_repo"].get_funds(session, account_id=account_id, asset_key="USD")
        assert funds["local_reserved"] == Decimal("40")

    # Tx D：UNKNOWN → PROVIDER_BOUND：local→provider 原子转移 + commit
    async with env["sessions"]() as session:
        await logic.ack_reservation(_UoW(session), reservation_id=res_id)
        funds = await env["exec_repo"].get_funds(session, account_id=account_id, asset_key="USD")
        assert funds["local_reserved"] == Decimal("0")
        assert funds["provider_reserved"] == Decimal("40")
        assert funds["available"] == funds["confirmed"] - funds["provider_reserved"] - funds["local_reserved"]
        await session.commit()

    # Tx E：PROVIDER_BOUND → CONSUMED：provider reserve 精确消耗 + commit
    async with env["sessions"]() as session:
        await logic.consume_reservation(_UoW(session), reservation_id=res_id)
        funds = await env["exec_repo"].get_funds(session, account_id=account_id, asset_key="USD")
        assert funds["provider_reserved"] == Decimal("0")
        assert funds["available"] == Decimal("100")
        await session.commit()
