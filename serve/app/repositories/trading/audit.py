"""Replay / audit Repository（WP-04 Checkpoint B）。

只拥有 SQL：replay_runs 的辅助读写。绝不 commit、不调用网络、不做业务判断。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession


def _rows(result) -> list[dict[str, Any]]:
    keys = list(result.keys())
    return [dict(zip(keys, row)) for row in result.fetchall()]


class AuditRepository:
    """replay/audit SQL；不持有状态。"""

    async def insert_replay_run(
        self,
        session: AsyncSession,
        *,
        run_key: str,
        replay_kind: str,
        manifest_hash: str,
        code_hash: str,
        seed: int,
        input_artifact_hash: str,
        output_artifact_hash: str,
        result: dict | None,
    ) -> tuple[int, bool]:
        """Claim and append one replay result atomically.

        ``run_key`` is the public idempotency key.  The claim owner is the hash of
        every immutable replay input/output field, so a retry returns the existing
        row while reuse of the key for different work fails closed.
        """
        from app.domain.trading.hashing import canonical_hash

        owner = canonical_hash(
            {
                "replay_kind": replay_kind,
                "manifest_hash": manifest_hash,
                "code_hash": code_hash,
                "seed": seed,
                "input_artifact_hash": input_artifact_hash,
                "output_artifact_hash": output_artifact_hash,
                "result": result,
            }
        )
        claim = await session.execute(
            text(
                "INSERT INTO trading.idempotency_claims (scope, key, owner) "
                "VALUES ('replay_run', :k, :owner) "
                "ON CONFLICT (scope, key) DO NOTHING RETURNING id"
            ),
            {"k": run_key, "owner": owner},
        )
        claimed = claim.scalar_one_or_none() is not None
        if not claimed:
            existing_owner = (
                await session.execute(
                    text(
                        "SELECT owner FROM trading.idempotency_claims "
                        "WHERE scope='replay_run' AND key=:k FOR UPDATE"
                    ),
                    {"k": run_key},
                )
            ).scalar_one()
            if existing_owner != owner:
                raise RuntimeError("replay_idempotency_conflict")
            existing = await self.get_replay_run(session, run_key)
            if existing is None:
                raise RuntimeError("replay_idempotency_claim_without_result")
            return int(existing["id"]), False

        statement = text(
                "INSERT INTO trading.replay_runs "
                "(run_key, replay_kind, manifest_hash, code_hash, seed, "
                " input_artifact_hash, output_artifact_hash, result) VALUES "
                "(:k, :rk, :mh, :ch, :seed, :ia, :oa, :res) RETURNING id"
            ).bindparams(bindparam("res", type_=JSONB()))
        res = await session.execute(
            statement,
            {
                "k": run_key, "rk": replay_kind, "mh": manifest_hash, "ch": code_hash,
                "seed": seed, "ia": input_artifact_hash, "oa": output_artifact_hash, "res": result,
            },
        )
        return int(res.scalar_one()), True

    async def metric_run_by_artifact_hash(
        self, session: AsyncSession, artifact_hash: str
    ) -> dict[str, Any] | None:
        """Return the single immutable COMPLETED metric artifact.

        A replay manifest is the metric artifact hash, never a mutable run key and
        never the output of an earlier replay.  Ambiguous content-addressed rows are
        accepted only when their complete frozen payload is identical.
        """
        result = await session.execute(
            text(
                "SELECT id, run_key, cohort_id, observation_ids, observation_set_hash, "
                "cohort_query_hash, strategy_version_id, "
                "release_manifest_id, label_versions, split, time_blocks, code_hash, "
                "config_hash, seed, n_market, n_episode, n_resolution_cluster, n_eff, "
                "results, ci, artifact_hash, status, completed_at, created_at "
                "FROM trading.metric_runs "
                "WHERE artifact_hash=:artifact_hash AND status='COMPLETED' "
                "ORDER BY id"
            ),
            {"artifact_hash": artifact_hash},
        )
        rows = _rows(result)
        if not rows:
            return None
        if len(rows) > 1:
            material = [
                {k: v for k, v in row.items() if k not in {"id", "created_at", "completed_at"}}
                for row in rows
            ]
            if any(item != material[0] for item in material[1:]):
                raise RuntimeError("replay_source_manifest_ambiguous")
        return rows[0]

    async def get_replay_run(self, session: AsyncSession, run_key: str) -> dict[str, Any] | None:
        result = await session.execute(
            text("SELECT * FROM trading.replay_runs WHERE run_key=:k"),
            {"k": run_key},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def list_replay_runs(
        self, session: AsyncSession, manifest_hash: str
    ) -> list[dict[str, Any]]:
        result = await session.execute(
            text(
                "SELECT * FROM trading.replay_runs WHERE manifest_hash=:mh "
                "ORDER BY id"
            ),
            {"mh": manifest_hash},
        )
        return _rows(result)


# ---------------- WP-05 Checkpoint C：workflow / external-call / alert（append-only） ----------------

    async def insert_workflow_event(
        self,
        session: AsyncSession,
        *,
        event_key: str,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload_hash: str,
        payload: dict,
    ) -> dict[str, Any]:
        from sqlalchemy import bindparam
        from sqlalchemy.dialects.postgresql import JSONB

        result = await session.execute(
            text(
                "INSERT INTO trading.workflow_events "
                "(event_key, event_type, aggregate_type, aggregate_id, payload_hash, payload) "
                "VALUES (:k, :et, :at, :aid, :ph, :payload) "
                "ON CONFLICT (event_key) DO NOTHING RETURNING *"
            ).bindparams(bindparam("payload", type_=JSONB())),
            {
                "k": event_key, "et": event_type, "at": aggregate_type, "aid": aggregate_id,
                "ph": payload_hash, "payload": payload,
            },
        )
        rows = _rows(result)
        if rows:
            return rows[0]
        existing = await session.execute(
            text("SELECT * FROM trading.workflow_events WHERE event_key=:k"),
            {"k": event_key},
        )
        existing_rows = _rows(existing)
        if not existing_rows:
            raise RuntimeError("workflow_event_claim_lost")
        return existing_rows[0]

    async def insert_external_call_attempt(
        self,
        session: AsyncSession,
        *,
        attempt_key: str,
        driver: str,
        endpoint: str,
        method: str,
        request_hash: str,
        response_hash: str | None,
        status_code: int | None,
        latency_ms: int,
        rate_limit_remaining: int | None,
        error_reason: str | None,
        fence_token: int,
    ) -> dict[str, Any]:
        result = await session.execute(
            text(
                "INSERT INTO trading.external_call_attempts "
                "(attempt_key, driver, endpoint, method, request_hash, response_hash, "
                " status_code, latency_ms, rate_limit_remaining, error_reason, fence_token) "
                "VALUES (:k, :dr, :ep, :m, :rh, :rsh, :sc, :lm, :rl, :er, :fence) "
                "ON CONFLICT (attempt_key) DO NOTHING RETURNING *"
            ),
            {
                "k": attempt_key, "dr": driver, "ep": endpoint, "m": method, "rh": request_hash,
                "rsh": response_hash, "sc": status_code, "lm": latency_ms, "rl": rate_limit_remaining,
                "er": error_reason, "fence": fence_token,
            },
        )
        rows = _rows(result)
        if rows:
            return rows[0]
        existing = await session.execute(
            text("SELECT * FROM trading.external_call_attempts WHERE attempt_key=:k"),
            {"k": attempt_key},
        )
        existing_rows = _rows(existing)
        if not existing_rows:
            raise RuntimeError("external_call_attempt_claim_lost")
        return existing_rows[0]

    async def insert_alert_event(
        self,
        session: AsyncSession,
        *,
        alert_key: str,
        severity: str,
        code: str,
        message_redacted: str,
    ) -> dict[str, Any]:
        result = await session.execute(
            text(
                "INSERT INTO trading.alert_events "
                "(alert_key, severity, code, message_redacted) "
                "VALUES (:k, :sev, :code, :msg) "
                "ON CONFLICT (alert_key) DO NOTHING RETURNING *"
            ),
            {"k": alert_key, "sev": severity, "code": code, "msg": message_redacted},
        )
        rows = _rows(result)
        if rows:
            return rows[0]
        existing = await session.execute(
            text("SELECT * FROM trading.alert_events WHERE alert_key=:k"),
            {"k": alert_key},
        )
        existing_rows = _rows(existing)
        if not existing_rows:
            raise RuntimeError("alert_event_claim_lost")
        return existing_rows[0]
