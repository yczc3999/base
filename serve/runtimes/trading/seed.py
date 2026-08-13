"""Pipeline 冻结配置种子（WP-07C Checkpoint B）。

pipeline 跑 G0/R0 的硬前置：DB 必须已有冻结的 objective / strategy / release /
cohort / policy_freeze。本模块幂等 bootstrap 一套最小 shadow 配置（按
test_v2_blind_forecast_workflow.py 的权威种子模板）。

幂等：按 contract_key/strategy_key/cohort_key 查存在则复用 id，不重复插入；
policy_freeze 按 (policy_type, scope, release) 查重。全部 append-only。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from sqlalchemy import text

from app.domain.trading.hashing import canonical_hash
from app.repositories.trading.cohort import REQUIRED_COHORT_POLICIES
from runtimes.trading.policies import build_shadow_policy_hashes

logger = logging.getLogger(__name__)

# 最小 shadow 目标（HOLD_TO_RESOLUTION，全成本口径；与测试模板一致）。
DEFAULT_OBJECTIVE = {
    "objective_fn_version": "objective/v1",
    "units": "USD",
    "decision_horizon": "HOLD_TO_RESOLUTION",
    "HOLD_TO_RESOLUTION": True,
    "discount_policy": {"kind": "none"},
    "capital_charge_policy": {"kind": "linear"},
    "NO_ACTION": {"action": "NO_ACTION"},
    "allowed_actions": ["NO_ACTION", "PREDICT"],
    **{
        field: {"included": True}
        for field in (
            "trading_cost_scope", "data_cost_scope", "llm_cost_scope",
            "search_cost_scope", "infrastructure_cost_scope",
            "human_cost_scope", "operational_cost_scope",
        )
    },
    "robustness_policy": {"kind": "worst_case"},
    "hard_constraint_ordering": ["eligibility", "capital"],
}

DEFAULT_COHORT_KEY = "cohort-shadow"


@dataclass(frozen=True)
class SeedResult:
    cohort_id: int
    objective_id: int
    strategy_id: int
    release_id: int
    created: bool  # True=本轮新建；False=复用已有


async def _scalar(session, sql: str, params: dict) -> int:
    return (await session.execute(text(sql), params)).scalar_one()


async def _find(session, sql: str, params: dict) -> int | None:
    row = (await session.execute(text(sql), params)).mappings().first()
    return row["id"] if row else None


async def ensure_pipeline_seed(session, *, cohort_key: str = DEFAULT_COHORT_KEY) -> SeedResult:
    """幂等确保 pipeline 冻结配置存在；返回 cohort/objective/strategy/release id。"""
    existing = await _find(
        session,
        "SELECT id FROM trading.evaluation_cohorts WHERE cohort_key=:k",
        {"k": cohort_key},
    )
    if existing is not None:
        row = (
            await session.execute(
                text(
                    "SELECT id, objective_contract_id, strategy_version_id, release_manifest_id "
                    "FROM trading.evaluation_cohorts WHERE cohort_key=:k"
                ),
                {"k": cohort_key},
            )
        ).mappings().one()
        return SeedResult(
            cohort_id=row["id"],
            objective_id=row["objective_contract_id"],
            strategy_id=row["strategy_version_id"],
            release_id=row["release_manifest_id"],
            created=False,
        )

    obj_hash = canonical_hash(DEFAULT_OBJECTIVE)
    objective_id = await _scalar(
        session,
        "INSERT INTO trading.strategy_objective_contracts "
        "(contract_key,version_no,content,schema_version,content_hash,status) "
        "VALUES (:k,1,CAST(:c AS jsonb),1,:h,'active') RETURNING id",
        {"k": f"{cohort_key}-objective", "c": json.dumps(DEFAULT_OBJECTIVE), "h": obj_hash},
    )
    strategy_content = {"strategy": "cognition/v1"}
    strategy_id = await _scalar(
        session,
        "INSERT INTO trading.strategy_versions "
        "(strategy_key,version_no,content,schema_version,content_hash,status) "
        "VALUES (:k,1,CAST(:c AS jsonb),1,:h,'active') RETURNING id",
        {"k": f"{cohort_key}-strategy", "c": json.dumps(strategy_content),
         "h": canonical_hash(strategy_content)},
    )
    permission_id = await _scalar(
        session,
        "INSERT INTO trading.capital_permission_manifests "
        "(name,mode,capability,limits,evaluation_capital,authorized_capital,content_hash,status) "
        "VALUES (:n,'shadow','{}','{}',0,0,:h,'active') RETURNING id",
        {"n": f"{cohort_key}-permission", "h": canonical_hash({"permission": cohort_key})},
    )
    config_id = await _scalar(
        session,
        "INSERT INTO trading.runtime_config_versions "
        "(config_key,version_no,content,schema_version,content_hash,status) "
        "VALUES (:k,1,'{}',1,:h,'active') RETURNING id",
        {"k": f"{cohort_key}-config", "h": canonical_hash({"config": cohort_key})},
    )
    execution_id = await _scalar(
        session,
        "INSERT INTO trading.execution_spec_versions "
        "(spec_key,version_no,content,schema_version,content_hash,status) "
        "VALUES (:k,1,'{}',1,:h,'active') RETURNING id",
        {"k": f"{cohort_key}-execution", "h": canonical_hash({"execution": cohort_key})},
    )
    release_id = await _scalar(
        session,
        "INSERT INTO trading.release_manifests "
        "(release_name,config_version_id,strategy_version_id,execution_spec_version_id,"
        "capital_permission_manifest_id,git_sha,image_digest,db_revision,total_hash,status) "
        "VALUES (:n,:cfg,:strategy,:execution,:permission,'pipeline','img','b1000072',:h,'active') "
        "RETURNING id",
        {"n": f"{cohort_key}-release", "cfg": config_id, "strategy": strategy_id,
         "execution": execution_id, "permission": permission_id,
         "h": canonical_hash({"release": cohort_key})},
    )
    policy_hashes = build_shadow_policy_hashes()
    for name in REQUIRED_COHORT_POLICIES:
        await session.execute(
            text(
                "INSERT INTO trading.policy_type_scopes (policy_type,scope_type,scope_key) "
                "VALUES (:n,'cohort',:k) ON CONFLICT DO NOTHING"
            ),
            {"n": name, "k": cohort_key},
        )
        await session.execute(
            text(
                "INSERT INTO trading.policy_freezes "
                "(policy_type,scope_type,scope_key,policy_version,policy_content_hash,"
                "release_manifest_id,status) VALUES (:n,'cohort',:k,1,:h,:r,'frozen')"
            ),
            {"n": name, "k": cohort_key, "h": policy_hashes[name], "r": release_id},
        )
    cohort_id = await _scalar(
        session,
        "INSERT INTO trading.evaluation_cohorts "
        "(cohort_key,status,objective_contract_id,strategy_version_id,release_manifest_id,"
        "policy_hashes,seed_hash) "
        "VALUES (:k,'OPEN',:obj,:strategy,:release,CAST(:p AS jsonb),:seed) RETURNING id",
        {"k": cohort_key, "obj": objective_id, "strategy": strategy_id,
         "release": release_id, "p": json.dumps(policy_hashes),
         "seed": canonical_hash({"seed": cohort_key})},
    )
    logger.info("pipeline_seed_created cohort=%s id=%s", cohort_key, cohort_id)
    return SeedResult(
        cohort_id=cohort_id, objective_id=objective_id,
        strategy_id=strategy_id, release_id=release_id, created=True,
    )
