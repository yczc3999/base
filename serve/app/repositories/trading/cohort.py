"""Cohort/Screening Repository（WP-01C Checkpoint B）。

只拥有 SQL：cohort、membership（NULL→COMPLETE confirmation）、screening episode、audit sample。
绝不 commit、不调用网络、不做业务判断。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

REQUIRED_COHORT_POLICIES = (
    "eligibility", "taxonomy", "horizon", "r0", "r1", "evidence_coverage",
    "shrinkage", "baseline_scoring", "split_inference", "reject_audit",
)


def _rows(result) -> list[dict[str, Any]]:
    keys = list(result.keys())
    return [dict(zip(keys, row)) for row in result.fetchall()]


class CohortRepository:
    """cohort/screening SQL；不持有状态。"""

    async def create_cohort(
        self,
        session: AsyncSession,
        *,
        cohort_key: str,
        objective_contract_id: int,
        strategy_version_id: int,
        release_manifest_id: int,
        policy_hashes: dict,
        seed_hash: str,
    ) -> int:
        insert_result = await session.execute(
            text(
                "INSERT INTO trading.evaluation_cohorts "
                "(cohort_key, status, objective_contract_id, strategy_version_id, "
                " release_manifest_id, policy_hashes, seed_hash) "
                "VALUES (:k, 'DRAFT', :obj, :strat, :rel, :pol, :seed) RETURNING id"
            ).bindparams(bindparam("pol", type_=JSONB())),
            {
                "k": cohort_key,
                "obj": objective_contract_id,
                "strat": strategy_version_id,
                "rel": release_manifest_id,
                "pol": policy_hashes,
                "seed": seed_hash,
            },
        )
        return insert_result.scalar_one()

    async def get_cohort(self, session: AsyncSession, cohort_id: int) -> dict[str, Any] | None:
        result = await session.execute(
            text("SELECT * FROM trading.evaluation_cohorts WHERE id=:c"),
            {"c": cohort_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def get_g0_context(
        self, session: AsyncSession, cohort_id: int
    ) -> dict[str, Any] | None:
        """读取 cohort 明确绑定的 objective/strategy/release；不做 latest/fallback。"""

        result = await session.execute(
            text(
                "SELECT c.*, "
                " o.contract_key AS objective_key, o.version_no AS objective_version, "
                " o.content AS objective_content, o.content_hash AS objective_hash, "
                " o.status AS objective_status, "
                " s.strategy_key, s.version_no AS strategy_version, "
                " s.content_hash AS strategy_hash, s.status AS strategy_status, "
                " r.release_name, r.total_hash AS release_hash, r.status AS release_status, "
                " r.strategy_version_id AS release_strategy_version_id "
                "FROM trading.evaluation_cohorts c "
                "JOIN trading.strategy_objective_contracts o ON o.id=c.objective_contract_id "
                "JOIN trading.strategy_versions s ON s.id=c.strategy_version_id "
                "JOIN trading.release_manifests r ON r.id=c.release_manifest_id "
                "WHERE c.id=:c"
            ),
            {"c": cohort_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def frozen_policies(
        self,
        session: AsyncSession,
        *,
        cohort_key: str,
        release_manifest_id: int,
        policy_hashes: dict[str, str],
    ) -> dict[str, dict[str, Any]]:
        """按 exact scope/key/release/content hash 读取 freeze，禁止层级 fallback。"""

        if not policy_hashes:
            return {}
        result = await session.execute(
            text(
                "SELECT pf.policy_type, pf.scope_type, pf.scope_key, pf.policy_version, "
                "       pf.policy_content_hash, pf.release_manifest_id, pf.frozen_at, pf.status "
                "FROM trading.policy_freezes pf "
                "JOIN trading.policy_type_scopes pts ON pts.policy_type=pf.policy_type "
                "WHERE pf.policy_type = ANY(:types) "
                "  AND pts.scope_type='cohort' AND pf.scope_type='cohort' "
                "  AND pf.scope_key=:scope_key AND pf.release_manifest_id=:release_id "
                "  AND pf.status IN ('frozen','released')"
            ),
            {
                "types": sorted(policy_hashes),
                "scope_key": cohort_key,
                "release_id": release_manifest_id,
            },
        )
        found: dict[str, dict[str, Any]] = {}
        for row in _rows(result):
            expected = policy_hashes.get(row["policy_type"])
            if expected == row["policy_content_hash"]:
                if row["policy_type"] in found:
                    raise RuntimeError(f"duplicate_frozen_policy:{row['policy_type']}")
                found[row["policy_type"]] = row
        return found

    async def get_complete_frame(
        self, session: AsyncSession, frame_id: int
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT id, status, total_markets, content_hash, artifact_ref, artifact_id, "
                "       completed_at "
                "FROM trading.pm_universe_frames "
                "WHERE id=:f AND status='COMPLETE'"
            ),
            {"f": frame_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def get_confirmed_membership(
        self, session: AsyncSession, *, cohort_id: int, market_id: int
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT um.*, f.content_hash AS frame_content_hash, "
                "       f.artifact_ref AS frame_artifact_ref, m.gamma_market_id AS market_key "
                "FROM trading.universe_memberships um "
                "JOIN trading.pm_universe_frames f "
                "  ON f.id=um.confirmed_frame_id AND f.status='COMPLETE' "
                "JOIN trading.pm_markets m ON m.id=um.market_id "
                "WHERE um.cohort_id=:c AND um.market_id=:m"
            ),
            {"c": cohort_id, "m": market_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def get_cohort_by_key(self, session: AsyncSession, cohort_key: str) -> dict[str, Any] | None:
        result = await session.execute(
            text("SELECT * FROM trading.evaluation_cohorts WHERE cohort_key=:k"),
            {"k": cohort_key},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def open_cohort(self, session: AsyncSession, cohort_id: int) -> bool:
        result = await session.execute(
            text(
                "UPDATE trading.evaluation_cohorts SET status='OPEN', opened_at=now() "
                "WHERE id=:c AND status='DRAFT'"
            ),
            {"c": cohort_id},
        )
        return result.rowcount == 1

    async def upsert_membership(
        self,
        session: AsyncSession,
        *,
        cohort_id: int,
        market_id: int,
        first_seen_source: str,
        first_observed_at: datetime,
        first_ingested_at: datetime,
        metadata_hash: str,
    ) -> bool:
        """``cohort×market`` 唯一；重复/乱序不产生 effect（effect=0）。"""
        result = await session.execute(
            text(
                "INSERT INTO trading.universe_memberships "
                "(cohort_id, market_id, first_seen_source, first_observed_at, first_ingested_at, "
                " metadata_hash) "
                "VALUES (:c, :m, :src, :fo, :fi, :mh) "
                "ON CONFLICT (cohort_id, market_id) DO NOTHING"
            ),
            {
                "c": cohort_id,
                "m": market_id,
                "src": first_seen_source,
                "fo": first_observed_at,
                "fi": first_ingested_at,
                "mh": metadata_hash,
            },
        )
        return result.rowcount == 1

    async def upsert_confirmed_memberships(
        self,
        session: AsyncSession,
        *,
        cohort_id: int,
        frame_id: int,
        first_observed_at: datetime,
        first_ingested_at: datetime,
        rows: list[dict[str, Any]],
    ) -> int:
        """Set-based REST enrollment/confirmation for a hydrated exact frame.

        Existing WS hints preserve every first-seen field and only gain the frame
        confirmation.  A prior confirmation to a different frame fails closed.
        """

        if not rows:
            return 0
        result = await session.execute(
            text(
                "WITH payload AS ("
                "  SELECT * FROM jsonb_to_recordset(:rows) "
                "  AS x(market_id bigint, metadata_hash text)"
                "), before_rows AS MATERIALIZED ("
                "  SELECT p.market_id, (u.id IS NOT NULL) AS existed, u.confirmed_frame_id "
                "  FROM payload p LEFT JOIN trading.universe_memberships u "
                "    ON u.cohort_id=:cohort AND u.market_id=p.market_id"
                "), upserted AS ("
                "  INSERT INTO trading.universe_memberships "
                "  (cohort_id,market_id,first_seen_source,first_observed_at,first_ingested_at,"
                "   metadata_hash,confirmed_frame_id,confirmed_at) "
                "  SELECT :cohort,p.market_id,'REST_FRAME',:observed,:ingested,p.metadata_hash,"
                "         :frame,now() FROM payload p "
                "  ON CONFLICT (cohort_id,market_id) DO UPDATE "
                "  SET confirmed_frame_id=:frame,confirmed_at=now() "
                "  WHERE universe_memberships.confirmed_frame_id IS NULL "
                "  RETURNING market_id"
                ") "
                "SELECT count(*) FILTER (WHERE NOT existed) AS inserted, "
                "       count(*) FILTER (WHERE confirmed_frame_id IS NOT NULL "
                "                        AND confirmed_frame_id<>:frame) AS conflicts, "
                "       count(*) FILTER (WHERE confirmed_frame_id=:frame) AS same_frame, "
                "       (SELECT count(*) FROM upserted) AS affected "
                "FROM before_rows"
            ).bindparams(bindparam("rows", type_=JSONB())),
            {
                "rows": rows,
                "cohort": cohort_id,
                "frame": frame_id,
                "observed": first_observed_at,
                "ingested": first_ingested_at,
            },
        )
        inserted, conflicts, same_frame, affected = result.one()
        if conflicts or affected + same_frame + conflicts != len(rows):
            raise RuntimeError("hydrated_frame_confirmation_conflict")
        return inserted

    async def get_confirmed_memberships(
        self, session: AsyncSession, *, cohort_id: int, market_ids: list[int]
    ) -> dict[int, dict[str, Any]]:
        if not market_ids:
            return {}
        result = await session.execute(
            text(
                "SELECT um.market_id,um.confirmed_frame_id,f.content_hash AS frame_content_hash,"
                "       f.artifact_ref AS frame_artifact_ref,m.gamma_market_id AS market_key "
                "FROM trading.universe_memberships um "
                "JOIN trading.pm_universe_frames f ON f.id=um.confirmed_frame_id "
                "                                      AND f.status='COMPLETE' "
                "JOIN trading.pm_markets m ON m.id=um.market_id "
                "WHERE um.cohort_id=:cohort AND um.market_id=ANY(:markets)"
            ),
            {"cohort": cohort_id, "markets": sorted(set(market_ids))},
        )
        return {row["market_id"]: row for row in _rows(result)}

    async def confirm_membership(
        self, session: AsyncSession, *, cohort_id: int, market_id: int, frame_id: int
    ) -> bool:
        """REST confirmation 只能 NULL→COMPLETE frame 原子补上（DB guard 兜底）。"""
        result = await session.execute(
            text(
                "UPDATE trading.universe_memberships "
                "SET confirmed_frame_id=:f, confirmed_at=now() "
                "WHERE cohort_id=:c AND market_id=:m AND confirmed_frame_id IS NULL "
                "  AND EXISTS (SELECT 1 FROM trading.pm_universe_frames WHERE id=:f AND status='COMPLETE')"
            ),
            {"c": cohort_id, "m": market_id, "f": frame_id},
        )
        return result.rowcount == 1

    async def count_unconfirmed_hints(
        self, session: AsyncSession, cohort_id: int
    ) -> int:
        result = await session.execute(
            text(
                "SELECT count(*) FROM trading.universe_memberships "
                "WHERE cohort_id=:c AND confirmed_frame_id IS NULL"
            ),
            {"c": cohort_id},
        )
        return result.scalar_one()

    async def insert_screening_episode(
        self,
        session: AsyncSession,
        *,
        cohort_id: int,
        market_id: int,
        episode_no: int,
        objective_contract_id: int,
        input_snapshot: dict,
        input_hash: str,
        result: str,
        reason_code: str | None,
        recheck_at: datetime | None,
        recheck_condition: str | None,
        audit_assigned: bool,
    ) -> int:
        insert_result = await session.execute(
            text(
                "INSERT INTO trading.screening_episodes "
                "(cohort_id, market_id, episode_no, objective_contract_id, input_snapshot, "
                " input_hash, result, reason_code, recheck_at, recheck_condition, audit_assigned) "
                "VALUES (:c, :m, :no, :obj, :inp, :ih, :res, :rc, :ra, :rcond, :aa) "
                "ON CONFLICT (cohort_id, market_id, episode_no) DO NOTHING RETURNING id"
            ).bindparams(bindparam("inp", type_=JSONB())),
            {
                "c": cohort_id,
                "m": market_id,
                "no": episode_no,
                "obj": objective_contract_id,
                "inp": input_snapshot,
                "ih": input_hash,
                "res": result,
                "rc": reason_code,
                "ra": recheck_at,
                "rcond": recheck_condition,
                "aa": audit_assigned,
            },
        )
        inserted = insert_result.scalar_one_or_none()
        if inserted is not None:
            return inserted
        existing_result = await session.execute(
            text(
                "SELECT id, objective_contract_id, input_hash, result, reason_code, "
                "       recheck_at, recheck_condition, audit_assigned "
                "FROM trading.screening_episodes "
                "WHERE cohort_id=:c AND market_id=:m AND episode_no=:no"
            ),
            {"c": cohort_id, "m": market_id, "no": episode_no},
        )
        existing = _rows(existing_result)[0]
        expected = {
            "objective_contract_id": objective_contract_id,
            "input_hash": input_hash,
            "result": result,
            "reason_code": reason_code,
            "recheck_at": recheck_at,
            "recheck_condition": recheck_condition,
            "audit_assigned": audit_assigned,
        }
        for key, value in expected.items():
            if existing[key] != value:
                raise RuntimeError(f"screening_episode_idempotency_conflict:{key}")
        return existing["id"]

    async def screening_count(self, session: AsyncSession, cohort_id: int) -> int:
        result = await session.execute(
            text("SELECT count(*) FROM trading.screening_episodes WHERE cohort_id=:c"),
            {"c": cohort_id},
        )
        return result.scalar_one()

    async def insert_screening_episodes_bulk(
        self, session: AsyncSession, *, cohort_id: int, rows: list[dict[str, Any]]
    ) -> dict[tuple[int, int], dict[str, Any]]:
        """Set-based idempotent screening insert used by the 50k hard contract."""

        if not rows:
            return {}
        # ``rows`` also carries Logic-only result fields (for example the exact
        # Decimal audit draw) used to assemble the return DTO.  Keep the JSONB
        # persistence boundary explicit instead of relying on
        # ``jsonb_to_recordset`` to ignore arbitrary/possibly non-JSON values.
        persisted_keys = (
            "market_id",
            "episode_no",
            "objective_contract_id",
            "input_snapshot",
            "input_hash",
            "result",
            "reason_code",
            "recheck_at",
            "recheck_condition",
            "audit_assigned",
        )
        payload_rows = [
            {key: row[key] for key in persisted_keys}
            for row in rows
        ]
        await session.execute(
            text(
                "WITH payload AS ("
                " SELECT * FROM jsonb_to_recordset(:rows) AS x("
                " market_id bigint,episode_no integer,objective_contract_id bigint,"
                " input_snapshot jsonb,input_hash text,result text,reason_code text,"
                " recheck_at timestamptz,recheck_condition text,audit_assigned boolean)"
                ") "
                " INSERT INTO trading.screening_episodes "
                " (cohort_id,market_id,episode_no,objective_contract_id,input_snapshot,input_hash,"
                "  result,reason_code,recheck_at,recheck_condition,audit_assigned) "
                " SELECT :cohort,market_id,episode_no,objective_contract_id,input_snapshot,input_hash,"
                "        result,reason_code,recheck_at,recheck_condition,audit_assigned FROM payload "
                " ON CONFLICT (cohort_id,market_id,episode_no) DO NOTHING"
            ).bindparams(bindparam("rows", type_=JSONB())),
            {"rows": payload_rows, "cohort": cohort_id},
        )
        # A data-modifying CTE and its sibling SELECT share one PostgreSQL
        # snapshot, so the sibling cannot observe rows just inserted into the
        # base table.  Query in the next statement to cover both first-write and
        # retry paths deterministically.
        result = await session.execute(
            text(
                "WITH payload AS ("
                " SELECT * FROM jsonb_to_recordset(:rows) AS x("
                " market_id bigint,episode_no integer)"
                ") "
                "SELECT se.id,se.market_id,se.episode_no,se.objective_contract_id,se.input_hash,"
                "       se.result,se.reason_code,se.recheck_at,se.recheck_condition,se.audit_assigned "
                "FROM trading.screening_episodes se JOIN payload p "
                " ON se.cohort_id=:cohort AND se.market_id=p.market_id "
                " AND se.episode_no=p.episode_no"
            ).bindparams(bindparam("rows", type_=JSONB())),
            {"rows": payload_rows, "cohort": cohort_id},
        )
        found = _rows(result)
        if len(found) != len(rows):
            raise RuntimeError("screening_episode_bulk_missing")
        expected = {(row["market_id"], row["episode_no"]): row for row in rows}
        for row in found:
            item = expected[(row["market_id"], row["episode_no"])]
            for key in (
                "objective_contract_id",
                "input_hash",
                "result",
                "reason_code",
                "recheck_condition",
                "audit_assigned",
            ):
                if row[key] != item[key]:
                    raise RuntimeError(f"screening_episode_idempotency_conflict:{key}")
        return {(row["market_id"], row["episode_no"]): row for row in found}

    async def insert_audit_sample(
        self,
        session: AsyncSession,
        *,
        cohort_id: int,
        target: str,
        content_hash: str,
        stratum: str,
        seed_hash: str,
        algorithm_hash: str,
        u: Any,
        inclusion_probability: Any,
        selected: bool,
    ) -> dict[str, Any]:
        result = await session.execute(
            text(
                "INSERT INTO trading.audit_samples "
                "(cohort_id, target, content_hash, stratum, seed_hash, algorithm_hash, "
                " u, inclusion_probability, selected) "
                "VALUES (:c, :t, :ch, :st, :sh, :ah, :u, :ip, :sel) "
                "ON CONFLICT (cohort_id, target, content_hash) DO NOTHING RETURNING id"
            ),
            {
                "c": cohort_id,
                "t": target,
                "ch": content_hash,
                "st": stratum,
                "sh": seed_hash,
                "ah": algorithm_hash,
                "u": u,
                "ip": inclusion_probability,
                "sel": selected,
            },
        )
        inserted = result.scalar_one_or_none()
        existing = await self.get_audit_sample(session, cohort_id, target, content_hash)
        if existing is None:  # pragma: no cover - INSERT/SELECT 同事务的不可能分支
            raise RuntimeError("audit_sample_missing_after_insert")
        expected = {
            "stratum": stratum,
            "seed_hash": seed_hash,
            "algorithm_hash": algorithm_hash,
            "u": u,
            "inclusion_probability": inclusion_probability,
            "selected": selected,
        }
        for key, value in expected.items():
            if existing[key] != value:
                raise RuntimeError(f"audit_sample_idempotency_conflict:{key}")
        existing["created"] = inserted is not None
        return existing

    async def insert_audit_samples_bulk(
        self, session: AsyncSession, *, cohort_id: int, rows: list[dict[str, Any]]
    ) -> None:
        if not rows:
            return
        result = await session.execute(
            text(
                "WITH payload AS ("
                " SELECT * FROM jsonb_to_recordset(:rows) AS x("
                " target text,content_hash text,stratum text,seed_hash text,algorithm_hash text,"
                " u numeric,inclusion_probability numeric,selected boolean)"
                ") "
                "INSERT INTO trading.audit_samples "
                "(cohort_id,target,content_hash,stratum,seed_hash,algorithm_hash,u,"
                " inclusion_probability,selected) "
                "SELECT :cohort,target,content_hash,stratum,seed_hash,algorithm_hash,u,"
                " inclusion_probability,selected FROM payload "
                "ON CONFLICT (cohort_id,target,content_hash) DO NOTHING"
            ).bindparams(bindparam("rows", type_=JSONB())),
            {"rows": rows, "cohort": cohort_id},
        )
        # Conflicts are verified by the subsequent screening FK/guard and replay
        # assertions; a first pass must insert every supplied unique sample.
        if result.rowcount not in (-1, len(rows), 0):
            raise RuntimeError("audit_sample_bulk_partial")

    async def get_audit_sample(
        self, session: AsyncSession, cohort_id: int, target: str, content_hash: str
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT * FROM trading.audit_samples "
                "WHERE cohort_id=:c AND target=:t AND content_hash=:ch"
            ),
            {"c": cohort_id, "t": target, "ch": content_hash},
        )
        rows = _rows(result)
        return rows[0] if rows else None
