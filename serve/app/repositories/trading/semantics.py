"""Semantics Repository（WP-01C Checkpoint A）。

只拥有 SQL：snapshot/spec/payout/component/schema/membership/edge 写入。绝不 commit、
不调用网络、不做业务判断（实施合同 §6）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession


def _rows(result) -> list[dict[str, Any]]:
    keys = list(result.keys())
    return [dict(zip(keys, row)) for row in result.fetchall()]


class SemanticsRepository:
    """semantics SQL；不持有状态。"""

    async def lock_contract_key(self, session: AsyncSession, contract_key: str) -> None:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 1101))"),
            {"k": contract_key},
        )

    async def lock_component_key(self, session: AsyncSession, component_key: str) -> None:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 1102))"),
            {"k": component_key},
        )

    async def token_mapping_context(
        self,
        session: AsyncSession,
        *,
        market_version_id: int,
        yes_token_version_id: int,
        no_token_version_id: int,
    ) -> dict[str, Any] | None:
        """一次读取 exact market-version 与 YES/NO token-version 绑定。"""

        result = await session.execute(
            text(
                "SELECT mv.market_id, mv.version_no AS market_version_no, "
                "       mv.normalized_hash AS market_version_hash, market.gamma_market_id AS market_key, "
                " ytv.token_id AS yes_token_id, ytv.version_no AS yes_token_version_no, "
                " ytv.outcome_index AS yes_outcome_index, yt.token_id AS yes_token_key, "
                " yt.market_id AS yes_market_id, "
                " ntv.token_id AS no_token_id, ntv.version_no AS no_token_version_no, "
                " ntv.outcome_index AS no_outcome_index, nt.token_id AS no_token_key, "
                " nt.market_id AS no_market_id "
                "FROM trading.pm_market_versions mv "
                "JOIN trading.pm_markets market ON market.id=mv.market_id "
                "JOIN trading.pm_token_versions ytv ON ytv.id=:ytv "
                "JOIN trading.pm_tokens yt ON yt.id=ytv.token_id "
                "JOIN trading.pm_token_versions ntv ON ntv.id=:ntv "
                "JOIN trading.pm_tokens nt ON nt.id=ntv.token_id "
                "WHERE mv.id=:mv"
            ),
            {"mv": market_version_id, "ytv": yes_token_version_id, "ntv": no_token_version_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def artifact_hash(
        self, session: AsyncSession, artifact_object_id: int
    ) -> str | None:
        result = await session.execute(
            text("SELECT sha256 FROM trading.artifact_objects WHERE id=:a"),
            {"a": artifact_object_id},
        )
        return result.scalar_one_or_none()

    async def get_snapshot_by_hash(
        self, session: AsyncSession, content_hash: str
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text("SELECT * FROM trading.contract_snapshots WHERE content_hash=:h ORDER BY id LIMIT 2"),
            {"h": content_hash},
        )
        rows = _rows(result)
        if len(rows) > 1:
            raise RuntimeError("snapshot_hash_not_unique")
        return rows[0] if rows else None

    async def get_spec_by_hash(
        self, session: AsyncSession, *, contract_key: str, content_hash: str
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT * FROM trading.contract_specs "
                "WHERE contract_key=:k AND content_hash=:h ORDER BY id LIMIT 2"
            ),
            {"k": contract_key, "h": content_hash},
        )
        rows = _rows(result)
        if len(rows) > 1:
            raise RuntimeError("contract_spec_hash_not_unique")
        return rows[0] if rows else None

    async def insert_snapshot(
        self,
        session: AsyncSession,
        *,
        market_version_id: int,
        yes_token_version_id: int,
        no_token_version_id: int,
        artifact_object_id: int,
        question: str | None,
        rules: str | None,
        clarification: str | None,
        resolution_source: str | None,
        cutoff_at: datetime | None,
        timezone_name: str | None,
        raw_outcome_mapping: dict | None,
        content_hash: str,
    ) -> int:
        result = await session.execute(
            text(
                "INSERT INTO trading.contract_snapshots "
                "(market_version_id, yes_token_version_id, no_token_version_id, artifact_object_id, "
                " question, rules, clarification, resolution_source, cutoff_at, timezone_name, "
                " raw_outcome_mapping, content_hash) "
                "VALUES (:mv, :ytv, :ntv, :art, :q, :r, :cl, :rs, :cut, :tz, :rom, :ch) "
                "RETURNING id"
            ).bindparams(bindparam("rom", type_=JSONB())),
            {
                "mv": market_version_id,
                "ytv": yes_token_version_id,
                "ntv": no_token_version_id,
                "art": artifact_object_id,
                "q": question,
                "r": rules,
                "cl": clarification,
                "rs": resolution_source,
                "cut": cutoff_at,
                "tz": timezone_name,
                "rom": raw_outcome_mapping,
                "ch": content_hash,
            },
        )
        return result.scalar_one()

    async def insert_spec(
        self,
        session: AsyncSession,
        *,
        contract_key: str,
        snapshot_id: int,
        resolution_states: list[str],
        token_ids: dict,
        token_count: int,
        state_count: int,
        compiler_version: str,
        schema_version: int,
        status: str,
        content_hash: str,
        g1_reason: str | None,
    ) -> int:
        result = await session.execute(
            text(
                "INSERT INTO trading.contract_specs "
                "(contract_key, version_no, snapshot_id, kc_resolution_states, token_ids, "
                " token_count, state_count, compiler_version, schema_version, status, "
                " content_hash, g1_reason) "
                "VALUES (:key, "
                "        (SELECT COALESCE(MAX(version_no),0)+1 FROM trading.contract_specs "
                "         WHERE contract_key=:key), "
                "        :snap, :states, :toks, :tc, :sc, :cv, :sv, :st, :ch, :g1) "
                "RETURNING id"
            ).bindparams(bindparam("states", type_=JSONB()), bindparam("toks", type_=JSONB())),
            {
                "key": contract_key,
                "snap": snapshot_id,
                "states": resolution_states,
                "toks": token_ids,
                "tc": token_count,
                "sc": state_count,
                "cv": compiler_version,
                "sv": schema_version,
                "st": status,
                "ch": content_hash,
                "g1": g1_reason,
            },
        )
        return result.scalar_one()

    async def latest_spec_status(
        self, session: AsyncSession, contract_key: str
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT id, status, content_hash FROM trading.contract_specs "
                "WHERE contract_key=:k ORDER BY version_no DESC LIMIT 1"
            ),
            {"k": contract_key},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def get_spec(self, session: AsyncSession, spec_id: int) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT id, contract_key, version_no, snapshot_id, kc_resolution_states, "
                "       token_ids, token_count, state_count, compiler_version, schema_version, "
                "       status, content_hash, g1_reason "
                "FROM trading.contract_specs WHERE id=:s"
            ),
            {"s": spec_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def get_specs(
        self, session: AsyncSession, spec_ids: list[int]
    ) -> dict[int, dict[str, Any]]:
        if not spec_ids:
            return {}
        result = await session.execute(
            text(
                "SELECT id, contract_key, kc_resolution_states, token_ids, token_count, "
                "       state_count, status, content_hash "
                "FROM trading.contract_specs WHERE id = ANY(:ids)"
            ),
            {"ids": sorted(set(spec_ids))},
        )
        return {row["id"]: row for row in _rows(result)}

    async def get_snapshot(self, session: AsyncSession, snapshot_id: int) -> dict[str, Any] | None:
        result = await session.execute(
            text("SELECT * FROM trading.contract_snapshots WHERE id=:s"),
            {"s": snapshot_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def insert_payout(
        self,
        session: AsyncSession,
        *,
        contract_spec_id: int,
        pm_token_id: int,
        token_version_id: int,
        outcome_index: int,
        function_ir: dict,
        test_vectors: dict,
        algorithm_hash: str,
        content_hash: str,
    ) -> None:
        await session.execute(
            text(
                "INSERT INTO trading.payout_functions "
                "(contract_spec_id, pm_token_id, token_version_id, outcome_index, function_ir, "
                " test_vectors, algorithm_hash, content_hash) "
                "VALUES (:spec, :tok, :tv, :oi, :ir, :tv2, :ah, :ch)"
            ).bindparams(bindparam("ir", type_=JSONB()), bindparam("tv2", type_=JSONB())),
            {
                "spec": contract_spec_id,
                "tok": pm_token_id,
                "tv": token_version_id,
                "oi": outcome_index,
                "ir": function_ir,
                "tv2": test_vectors,
                "ah": algorithm_hash,
                "ch": content_hash,
            },
        )

    async def payouts_for_spec(
        self, session: AsyncSession, contract_spec_id: int
    ) -> list[dict[str, Any]]:
        result = await session.execute(
            text(
                "SELECT pm_token_id, token_version_id, outcome_index, function_ir, "
                "       test_vectors, algorithm_hash, content_hash "
                "FROM trading.payout_functions WHERE contract_spec_id=:s "
                "ORDER BY outcome_index, pm_token_id"
            ),
            {"s": contract_spec_id},
        )
        return _rows(result)

    async def insert_component(
        self, session: AsyncSession, *, component_key: str, cost_budget: Any, description: str | None
    ) -> int:
        result = await session.execute(
            text(
                "INSERT INTO trading.forecast_components (component_key, cost_budget, description) "
                "VALUES (:k, :cb, :d) RETURNING id"
            ),
            {"k": component_key, "cb": cost_budget, "d": description},
        )
        return result.scalar_one()

    async def get_component(self, session: AsyncSession, component_key: str) -> dict[str, Any] | None:
        result = await session.execute(
            text("SELECT * FROM trading.forecast_components WHERE component_key=:k"),
            {"k": component_key},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def get_world_schema_by_hash(
        self, session: AsyncSession, *, component_id: int, content_hash: str
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT * FROM trading.world_schema_versions "
                "WHERE component_id=:c AND content_hash=:h ORDER BY id LIMIT 2"
            ),
            {"c": component_id, "h": content_hash},
        )
        rows = _rows(result)
        if len(rows) > 1:
            raise RuntimeError("world_schema_hash_not_unique")
        return rows[0] if rows else None

    async def get_component_version_by_hash(
        self, session: AsyncSession, *, component_id: int, content_hash: str
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT * FROM trading.forecast_component_versions "
                "WHERE component_id=:c AND content_hash=:h ORDER BY id LIMIT 2"
            ),
            {"c": component_id, "h": content_hash},
        )
        rows = _rows(result)
        if len(rows) > 1:
            raise RuntimeError("component_version_hash_not_unique")
        return rows[0] if rows else None

    async def component_members(
        self, session: AsyncSession, component_version_id: int
    ) -> list[dict[str, Any]]:
        result = await session.execute(
            text(
                "SELECT m.contract_spec_id, m.h_c, m.totality_test_hash, s.content_hash, "
                "       s.kc_resolution_states "
                "FROM trading.forecast_component_contract_specs m "
                "JOIN trading.contract_specs s ON s.id=m.contract_spec_id "
                "WHERE m.component_version_id=:cv ORDER BY s.content_hash, m.contract_spec_id"
            ),
            {"cv": component_version_id},
        )
        return _rows(result)

    async def insert_world_schema(
        self,
        session: AsyncSession,
        *,
        component_id: int,
        variables: dict,
        domains: dict,
        constraints: list,
        factorization: dict,
        world_states: list,
        state_count: int,
        resolution_map: dict,
        h_c: dict,
        status: str,
        content_hash: str,
        schema_version: int,
    ) -> int:
        result = await session.execute(
            text(
                "INSERT INTO trading.world_schema_versions "
                "(component_id, version_no, variables, domains, constraints, factorization, "
                " world_states, state_count, resolution_map, h_c, status, content_hash, schema_version) "
                "VALUES (:c, "
                "        (SELECT COALESCE(MAX(version_no),0)+1 FROM trading.world_schema_versions "
                "         WHERE component_id=:c), "
                "        :v, :d, :con, :f, :ws, :sc, :rm, :hc, :st, :ch, :sv) "
                "RETURNING id"
            ).bindparams(bindparam("v", type_=JSONB()), bindparam("d", type_=JSONB()),
                         bindparam("con", type_=JSONB()), bindparam("f", type_=JSONB()),
                         bindparam("ws", type_=JSONB()), bindparam("rm", type_=JSONB()),
                         bindparam("hc", type_=JSONB())),
            {
                "c": component_id,
                "v": variables,
                "d": domains,
                "con": constraints,
                "f": factorization,
                "ws": world_states,
                "sc": state_count,
                "rm": resolution_map,
                "hc": h_c,
                "st": status,
                "ch": content_hash,
                "sv": schema_version,
            },
        )
        return result.scalar_one()

    async def insert_component_version(
        self,
        session: AsyncSession,
        *,
        component_id: int,
        world_schema_version_id: int,
        status: str,
        content_hash: str,
        cost_budget: Any,
    ) -> int:
        result = await session.execute(
            text(
                "INSERT INTO trading.forecast_component_versions "
                "(component_id, version_no, world_schema_version_id, status, content_hash, cost_budget) "
                "VALUES (:c, "
                "        (SELECT COALESCE(MAX(version_no),0)+1 FROM trading.forecast_component_versions "
                "         WHERE component_id=:c), "
                "        :wsv, :st, :ch, :cb) "
                "RETURNING id"
            ),
            {"c": component_id, "wsv": world_schema_version_id, "st": status, "ch": content_hash, "cb": cost_budget},
        )
        return result.scalar_one()

    async def insert_component_member(
        self,
        session: AsyncSession,
        *,
        component_version_id: int,
        contract_spec_id: int,
        h_c: dict,
        totality_test_hash: str | None,
    ) -> None:
        await session.execute(
            text(
                "INSERT INTO trading.forecast_component_contract_specs "
                "(component_version_id, contract_spec_id, h_c, totality_test_hash) "
                "VALUES (:cv, :spec, :hc, :th)"
            ).bindparams(bindparam("hc", type_=JSONB())),
            {"cv": component_version_id, "spec": contract_spec_id, "hc": h_c, "th": totality_test_hash},
        )

    async def component_member_spec_ids(
        self, session: AsyncSession, component_version_id: int
    ) -> list[int]:
        result = await session.execute(
            text(
                "SELECT contract_spec_id FROM trading.forecast_component_contract_specs "
                "WHERE component_version_id=:cv ORDER BY contract_spec_id"
            ),
            {"cv": component_version_id},
        )
        return [r[0] for r in result.fetchall()]

    async def latest_component_version(
        self, session: AsyncSession, component_key: str
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT cv.id, cv.version_no, cv.world_schema_version_id, cv.status, cv.content_hash "
                "FROM trading.forecast_component_versions cv "
                "JOIN trading.forecast_components c ON c.id = cv.component_id "
                "WHERE c.component_key=:k ORDER BY cv.version_no DESC LIMIT 1"
            ),
            {"k": component_key},
        )
        rows = _rows(result)
        return rows[0] if rows else None
