"""Settlement / label Repository（WP-04 Checkpoint B）。

只拥有 SQL：resolution label revision、resolution cluster、score target 的辅助读写。
绝不 commit、不调用网络、不做业务判断。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession


def _rows(result) -> list[dict[str, Any]]:
    keys = list(result.keys())
    return [dict(zip(keys, row)) for row in result.fetchall()]


class SettlementRepository:
    """settlement SQL；不持有状态。"""

    # ---------------- resolution labels ----------------

    async def get_label_current(
        self, session: AsyncSession, contract_spec_id: int, label_key: str
    ) -> dict[str, Any] | None:
        """返回当前（未被 supersede 的）label revision。"""
        result = await session.execute(
            text(
                "SELECT * FROM trading.resolution_labels r "
                "WHERE r.contract_spec_id=:cs AND r.label_key=:k "
                "  AND NOT EXISTS ("
                "    SELECT 1 FROM trading.resolution_labels s "
                "     WHERE s.contract_spec_id=r.contract_spec_id "
                "       AND s.label_key=r.label_key AND s.supersedes_id=r.id)"
                "ORDER BY r.version_no DESC LIMIT 1"
            ),
            {"cs": contract_spec_id, "k": label_key},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def get_label_by_version(
        self, session: AsyncSession, label_id: int
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text("SELECT * FROM trading.resolution_labels WHERE id=:id"),
            {"id": label_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def insert_label_revision(
        self,
        session: AsyncSession,
        *,
        contract_spec_id: int,
        label_key: str,
        version_no: int,
        state: str,
        resolution_state: str | None,
        resolution_source: str | None,
        evidence_artifact_id: int | None,
        raw_outcome: dict | None,
        token_cashflow: dict | None,
        policy_code_hash: str,
        supersedes_id: int | None,
        auditor_identity: str | None,
        exclusion_reason: str | None,
        conflict_set: list | None,
    ) -> int:
        result = await session.execute(
            text(
                "INSERT INTO trading.resolution_labels "
                "(contract_spec_id, label_key, version_no, state, resolution_state, "
                " resolution_source, evidence_artifact_id, raw_outcome, token_cashflow, "
                " policy_code_hash, supersedes_id, auditor_identity, exclusion_reason, "
                " conflict_set) VALUES "
                "(:cs, :k, :v, :st, :rs, :rsc, :ea, :ro, :tc, :ph, :sup, :au, :er, :cf) "
                "RETURNING id"
            ),
            {
                "cs": contract_spec_id, "k": label_key, "v": version_no, "st": state,
                "rs": resolution_state, "rsc": resolution_source, "ea": evidence_artifact_id,
                "ro": raw_outcome, "tc": token_cashflow, "ph": policy_code_hash,
                "sup": supersedes_id, "au": auditor_identity, "er": exclusion_reason,
                "cf": conflict_set,
            },
        )
        return result.scalar_one()

    # ---------------- resolution clusters ----------------

    async def get_cluster(
        self, session: AsyncSession, *, cluster_key: str, cluster_version: int
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT * FROM trading.resolution_clusters "
                "WHERE cluster_key=:k AND cluster_version=:v"
            ),
            {"k": cluster_key, "v": cluster_version},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def insert_cluster(
        self,
        session: AsyncSession,
        *,
        cluster_key: str,
        cluster_version: int,
        split: str,
        time_block_start: datetime,
        time_block_end: datetime,
        horizon: str,
        status: str,
    ) -> int:
        result = await session.execute(
            text(
                "INSERT INTO trading.resolution_clusters "
                "(cluster_key, cluster_version, split, time_block_start, time_block_end, "
                " horizon, status) VALUES "
                "(:k, :v, :sp, :tbs, :tbe, :h, :st) "
                "ON CONFLICT (cluster_key, cluster_version) DO NOTHING RETURNING id"
            ),
            {"k": cluster_key, "v": cluster_version, "sp": split, "tbs": time_block_start,
             "tbe": time_block_end, "h": horizon, "st": status},
        )
        inserted = result.scalar_one_or_none()
        if inserted is not None:
            return inserted
        existing = await self.get_cluster(session, cluster_key=cluster_key, cluster_version=cluster_version)
        if existing is None:
            raise RuntimeError("resolution_cluster_missing_after_insert")
        return existing["id"]

    async def insert_cluster_membership(
        self,
        session: AsyncSession,
        *,
        resolution_cluster_id: int,
        contract_spec_id: int,
        token_id: int,
    ) -> None:
        await session.execute(
            text(
                "INSERT INTO trading.resolution_cluster_memberships "
                "(resolution_cluster_id, contract_spec_id, token_id) "
                "VALUES (:c, :cs, :t)"
            ),
            {"c": resolution_cluster_id, "cs": contract_spec_id, "t": token_id},
        )

    # ---------------- score targets ----------------

    async def insert_score_target(
        self,
        session: AsyncSession,
        *,
        target_key: str,
        target_type: str,
        contract_spec_id: int,
        resolution_cluster_id: int,
        horizon: str,
        target_weight: Any,
        payout_function_id: int | None,
        canonical_side: str | None,
        members: list | None,
        payout_type: str | None,
    ) -> int:
        result = await session.execute(
            text(
                "INSERT INTO trading.score_targets "
                "(target_key, target_type, contract_spec_id, resolution_cluster_id, "
                " horizon, target_weight, payout_function_id, canonical_side, members, "
                " payout_type) VALUES "
                "(:k, :tt, :cs, :rc, :h, :tw, :pf, :csd, :m, :pt) "
                "ON CONFLICT (target_key) DO NOTHING RETURNING id"
            ).bindparams(bindparam("m", type_=JSONB())),
            {"k": target_key, "tt": target_type, "cs": contract_spec_id,
             "rc": resolution_cluster_id, "h": horizon, "tw": target_weight,
             "pf": payout_function_id,
             "csd": canonical_side, "m": members, "pt": payout_type},
        )
        inserted = result.scalar_one_or_none()
        if inserted is not None:
            return inserted
        existing = await session.execute(
            text("SELECT id FROM trading.score_targets WHERE target_key=:k"),
            {"k": target_key},
        )
        return existing.scalar_one()

    async def insert_score_target_membership(
        self,
        session: AsyncSession,
        *,
        score_target_id: int,
        token_id: int,
        member_weight: Any,
    ) -> None:
        await session.execute(
            text(
                "INSERT INTO trading.score_target_memberships "
                "(score_target_id, token_id, member_weight) VALUES (:t, :tk, :w)"
            ),
            {"t": score_target_id, "tk": token_id, "w": member_weight},
        )


# ======================================================================
# WP-06 Checkpoint B —— chain-settlement repositories（revision ``b1000052``）
# 只拥有 SQL；绝不 commit、不调用网络、不做业务判断。
# ======================================================================


class ContractRegistryRepository:
    """contract_registry 只 INSERT（append-only；同 chain+kind 唯一 active）。"""

    async def insert_registry_entry(self, session: AsyncSession, row: dict[str, Any]) -> int:
        """发布一条 registry 条目（发布前 completeness trigger 校验）。返回 id。"""
        result = await session.execute(
            text(
                """
                INSERT INTO trading.contract_registry (
                    registry_version, kind, version_no, chain_id, address,
                    proxy_kind, runtime_keccak, resolved_implementation_or_beacon,
                    resolved_code_keccak, snapshot_block_number, snapshot_block_hash,
                    source_url, retrieved_at, content_hash, extra, status
                ) VALUES (
                    :registry_version, :kind, :version_no, :chain_id, :address,
                    :proxy_kind, :runtime_keccak, :resolved_implementation_or_beacon,
                    :resolved_code_keccak, :snapshot_block_number, :snapshot_block_hash,
                    :source_url, :retrieved_at, :content_hash, :extra, 'ACTIVE'
                )
                RETURNING id
                """
            ),
            {
                "registry_version": row["registry_version"],
                "kind": row["kind"],
                "version_no": row["version_no"],
                "chain_id": row["chain_id"],
                "address": row["address"],
                "proxy_kind": row["proxy_kind"],
                "runtime_keccak": row["runtime_keccak"],
                "resolved_implementation_or_beacon": row.get("resolved_implementation_or_beacon"),
                "resolved_code_keccak": row["resolved_code_keccak"],
                "snapshot_block_number": row["snapshot_block_number"],
                "snapshot_block_hash": row["snapshot_block_hash"],
                "source_url": row["source_url"],
                "retrieved_at": row["retrieved_at"],
                "content_hash": row["content_hash"],
                "extra": json.dumps(row.get("extra")) if row.get("extra") is not None else None,
            },
        )
        return int(result.scalar_one())

    async def get_active(self, session: AsyncSession, *, chain_id: int, kind: str) -> dict[str, Any] | None:
        """返回同 chain+kind 的当前 active 条目。"""
        result = await session.execute(
            text(
                """
                SELECT id, registry_version, kind, version_no, chain_id, address,
                       proxy_kind, runtime_keccak, resolved_implementation_or_beacon,
                       resolved_code_keccak, snapshot_block_number, snapshot_block_hash,
                       source_url, retrieved_at, content_hash, extra, status
                  FROM trading.contract_registry
                 WHERE chain_id = :chain_id AND kind = :kind AND status = 'ACTIVE'
                 ORDER BY version_no DESC LIMIT 1
                """
            ),
            {"chain_id": chain_id, "kind": kind},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def get_by_id(self, session: AsyncSession, registry_id: int) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                """
                SELECT id, registry_version, kind, version_no, chain_id, address,
                       proxy_kind, runtime_keccak, resolved_implementation_or_beacon,
                       resolved_code_keccak, snapshot_block_number, snapshot_block_hash,
                       source_url, retrieved_at, content_hash, extra, status
                  FROM trading.contract_registry WHERE id = :rid
                """
            ),
            {"rid": registry_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def verify_exact_version(self, session: AsyncSession, *, chain_id: int,
                                   kind: str, expected_content_hash: str) -> bool:
        """registry 版本 exact 复核：active 条目 content_hash 必须与期望全等。"""
        entry = await self.get_active(session, chain_id=chain_id, kind=kind)
        if entry is None:
            return False
        return entry["content_hash"] == expected_content_hash


class ChainOperationRepository:
    """chain_operations 只经受控路径变更（状态机由 CAS 触发推进）。"""

    async def insert_operation(self, session: AsyncSession, row: dict[str, Any]) -> int:
        """创建 PREPARED operation；同 key 异参由唯一约束硬冲突。返回 id。"""
        result = await session.execute(
            text(
                """
                INSERT INTO trading.chain_operations (
                    operation_key, idempotency_key, economic_hash, operation_type,
                    chain_id, account_id, wallet_address, condition_id, market_id,
                    registry_version_id, target_address, permission_ref,
                    release_manifest_id, capital_permission_manifest_id,
                    fencing_token, amount_base_units, calldata, calldata_keccak,
                    body_hash, call_set_hash, expected_operation_hash,
                    preflight_hash1, preflight_hash2
                ) VALUES (
                    :operation_key, :idempotency_key, :economic_hash, :operation_type,
                    :chain_id, :account_id, :wallet_address, :condition_id, :market_id,
                    :registry_version_id, :target_address, :permission_ref,
                    :release_manifest_id, :capital_permission_manifest_id,
                    :fencing_token, :amount_base_units, :calldata, :calldata_keccak,
                    :body_hash, :call_set_hash, :expected_operation_hash,
                    :preflight_hash1, :preflight_hash2
                )
                RETURNING id
                """
            ),
            {
                "operation_key": row["operation_key"],
                "idempotency_key": row["idempotency_key"],
                "economic_hash": row["economic_hash"],
                "operation_type": row["operation_type"],
                "chain_id": row["chain_id"],
                "account_id": row["account_id"],
                "wallet_address": row["wallet_address"],
                "condition_id": row["condition_id"],
                "market_id": row.get("market_id"),
                "registry_version_id": row["registry_version_id"],
                "target_address": row["target_address"],
                "permission_ref": row["permission_ref"],
                "release_manifest_id": row["release_manifest_id"],
                "capital_permission_manifest_id": row["capital_permission_manifest_id"],
                "fencing_token": row["fencing_token"],
                "amount_base_units": row["amount_base_units"],
                "calldata": row["calldata"],
                "calldata_keccak": row["calldata_keccak"],
                "body_hash": row["body_hash"],
                "call_set_hash": row["call_set_hash"],
                "expected_operation_hash": row["expected_operation_hash"],
                "preflight_hash1": row["preflight_hash1"],
                "preflight_hash2": row["preflight_hash2"],
            },
        )
        return int(result.scalar_one())

    async def get_operation(self, session: AsyncSession, operation_id: int) -> dict[str, Any] | None:
        result = await session.execute(
            text("SELECT * FROM trading.chain_operations WHERE id = :oid"),
            {"oid": operation_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def get_by_key(self, session: AsyncSession, operation_key: str) -> dict[str, Any] | None:
        result = await session.execute(
            text("SELECT * FROM trading.chain_operations WHERE operation_key = :key"),
            {"key": operation_key},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def get_for_update(self, session: AsyncSession, operation_id: int) -> dict[str, Any] | None:
        result = await session.execute(
            text("SELECT * FROM trading.chain_operations WHERE id = :oid FOR UPDATE"),
            {"oid": operation_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def update_evidence(self, session: AsyncSession, operation_id: int,
                              fields: dict[str, Any]) -> None:
        """只更新 wire evidence 列（不触状态；状态机只能由 history CAS 触发推进）。"""
        allowed = {
            "relayer_nonce", "deadline", "transaction_id", "transaction_hash",
            "receipt_block_number", "receipt_block_hash", "finalized_block_number",
            "pre_balance", "post_balance",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"chain_operation evidence columns not allowed: {sorted(unknown)}")
        assignments = ", ".join(f"{col} = :{col}" for col in fields)
        params = dict(fields)
        for col in ("pre_balance", "post_balance"):
            if col in params and params[col] is not None:
                params[col] = json.dumps(params[col])
        params["oid"] = operation_id
        await session.execute(
            text(
                f"UPDATE trading.chain_operations SET {assignments}, updated_at = now() "
                f"WHERE id = :oid"
            ),
            params,
        )

    async def append_state_event(self, session: AsyncSession, event: dict[str, Any]) -> None:
        """append 状态机事件；CAS 触发校验 transition_from=fence 并推进 aggregate。"""
        await session.execute(
            text(
                """
                INSERT INTO trading.chain_operation_state_history (
                    operation_id, sequence_no, transition_from, transition_to,
                    event_type, event_payload, event_hash, fence_token
                ) VALUES (
                    :operation_id, :sequence_no, :transition_from, :transition_to,
                    :event_type, :event_payload, :event_hash, :fence_token
                )
                """
            ),
            {
                "operation_id": event["operation_id"],
                "sequence_no": event["sequence_no"],
                "transition_from": event["transition_from"],
                "transition_to": event["transition_to"],
                "event_type": event["event_type"],
                "event_payload": json.dumps(event["event_payload"]),
                "event_hash": event["event_hash"],
                "fence_token": event["fence_token"],
            },
        )

    async def next_sequence(self, session: AsyncSession, operation_id: int) -> int:
        result = await session.execute(
            text(
                "SELECT COALESCE(MAX(sequence_no), -1) + 1 FROM "
                "trading.chain_operation_state_history WHERE operation_id = :oid"
            ),
            {"oid": operation_id},
        )
        return int(result.scalar_one())

    async def list_recoverable(self, session: AsyncSession, *, limit: int = 200
                               ) -> list[dict[str, Any]]:
        """UNKNOWN 与未决 active operation（恢复只读查询用，禁盲重发）。"""
        result = await session.execute(
            text(
                """
                SELECT * FROM trading.chain_operations
                 WHERE status IN ('UNKNOWN','RELAYER_NEW','EXECUTED','MINED',
                                  'RELAYER_CONFIRMED','MINED_PROVISIONAL','REORGED')
                 ORDER BY id LIMIT :limit
                """
            ),
            {"limit": limit},
        )
        return _rows(result)

    async def claim_idempotency(self, session: AsyncSession, *, key: str,
                                owner: str) -> bool:
        """非分区 idempotency_claims：并发 claim 由唯一约束决定，不能靠先查后写。"""
        try:
            await session.execute(
                text(
                    """
                    INSERT INTO trading.idempotency_claims (scope, key, owner)
                    VALUES ('chain_operation', :key, :owner)
                    """
                ),
                {"key": key, "owner": owner},
            )
            return True
        except Exception:
            return False


class SettlementObservationRepository:
    """settlement_observations append-only；COMPLETE 五元组由 deferred trigger 核验。"""

    async def insert_observation(self, session: AsyncSession, row: dict[str, Any]) -> int:
        result = await session.execute(
            text(
                """
                INSERT INTO trading.settlement_observations (
                    observation_key, source_kind, condition_id, market_id, token_set,
                    outcome_index, numerator, denominator, winner, is_50_50_outcome,
                    redeemable, label_audit_version, as_of, received_at,
                    raw_artifact_ref, raw_artifact_hash, content_hash, status
                ) VALUES (
                    :observation_key, :source_kind, :condition_id, :market_id, :token_set,
                    :outcome_index, :numerator, :denominator, :winner, :is_50_50_outcome,
                    :redeemable, :label_audit_version, :as_of, :received_at,
                    :raw_artifact_ref, :raw_artifact_hash, :content_hash, :status
                )
                RETURNING id
                """
            ),
            {
                "observation_key": row["observation_key"],
                "source_kind": row["source_kind"],
                "condition_id": row["condition_id"],
                "market_id": row.get("market_id"),
                "token_set": json.dumps(row["token_set"]),
                "outcome_index": row.get("outcome_index"),
                "numerator": row.get("numerator"),
                "denominator": row.get("denominator"),
                "winner": row.get("winner"),
                "is_50_50_outcome": row.get("is_50_50_outcome"),
                "redeemable": row.get("redeemable"),
                "label_audit_version": row.get("label_audit_version"),
                "as_of": row["as_of"],
                "received_at": row["received_at"],
                "raw_artifact_ref": row.get("raw_artifact_ref"),
                "raw_artifact_hash": row["raw_artifact_hash"],
                "content_hash": row["content_hash"],
                "payload": json.dumps(row.get("payload")) if row.get("payload") is not None else None,
                "status": row.get("status", "PENDING"),
            },
        )
        return int(result.scalar_one())

    async def get_observations(self, session: AsyncSession, condition_id: str
                               ) -> list[dict[str, Any]]:
        result = await session.execute(
            text(
                """
                SELECT * FROM trading.settlement_observations
                 WHERE condition_id = :cid ORDER BY id
                """
            ),
            {"cid": condition_id},
        )
        return _rows(result)
