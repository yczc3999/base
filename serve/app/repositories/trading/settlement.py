"""Settlement / label Repository（WP-04 Checkpoint B）。

只拥有 SQL：resolution label revision、resolution cluster、score target 的辅助读写。
绝不 commit、不调用网络、不做业务判断。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import bindparam, text
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
        """Atomically supersede the prior active version and publish this entry."""
        result = await session.execute(
            text(
                """
                SELECT trading.v2_publish_contract_registry(
                    :registry_version, :kind, :version_no, :chain_id, :address,
                    :proxy_kind, :runtime_keccak, :resolved_implementation_or_beacon,
                    :resolved_code_keccak, :snapshot_block_number, :snapshot_block_hash,
                    :source_url, :retrieved_at, :content_hash, CAST(:extra AS jsonb))
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
                    registry_version_id, target_address, permission_ref, lease_owner,
                    release_manifest_id, capital_permission_manifest_id,
                    fencing_token, amount_base_units, calldata, calldata_keccak,
                    body_hash, call_set_hash, expected_operation_hash,
                    preflight_hash1, preflight_hash2, registry_content_hash,
                    registry_bundle, registry_bundle_content_hash,
                    registry_evidence_artifact_id, registry_evidence_hash,
                    geo_evidence_artifact_id, geo_evidence_hash, geo_allowed,
                    geo_observed_at, geo_source_version,
                    settlement_set_key, settlement_allocation, settlement_allocation_hash
                    , pre_balance
                ) VALUES (
                    :operation_key, :idempotency_key, :economic_hash, :operation_type,
                    :chain_id, :account_id, :wallet_address, :condition_id, :market_id,
                    :registry_version_id, :target_address, :permission_ref, :lease_owner,
                    :release_manifest_id, :capital_permission_manifest_id,
                    :fencing_token, :amount_base_units, :calldata, :calldata_keccak,
                    :body_hash, :call_set_hash, :expected_operation_hash,
                    :preflight_hash1, :preflight_hash2, :registry_content_hash,
                    CAST(:registry_bundle AS jsonb), :registry_bundle_content_hash,
                    :registry_evidence_artifact_id, :registry_evidence_hash,
                    :geo_evidence_artifact_id, :geo_evidence_hash, :geo_allowed,
                    :geo_observed_at, :geo_source_version,
                    :settlement_set_key, CAST(:settlement_allocation AS jsonb),
                    :settlement_allocation_hash, CAST(:pre_balance AS jsonb)
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
                "lease_owner": row["lease_owner"],
                "registry_content_hash": row["registry_content_hash"],
                "registry_bundle": json.dumps(row["registry_bundle"]),
                "registry_bundle_content_hash": row["registry_bundle_content_hash"],
                "registry_evidence_artifact_id": row["registry_evidence_artifact_id"],
                "registry_evidence_hash": row["registry_evidence_hash"],
                "geo_evidence_artifact_id": row["geo_evidence_artifact_id"],
                "geo_evidence_hash": row["geo_evidence_hash"],
                "geo_allowed": row["geo_allowed"],
                "geo_observed_at": row["geo_observed_at"],
                "geo_source_version": row["geo_source_version"],
                "settlement_set_key": row["settlement_set_key"],
                "settlement_allocation": json.dumps(row["settlement_allocation"]),
                "settlement_allocation_hash": row["settlement_allocation_hash"],
                "pre_balance": json.dumps(row["pre_balance"]),
            },
        )
        return int(result.scalar_one())

    async def load_preflight_context(
        self,
        session: AsyncSession,
        *,
        account_id: int,
        market_id: int,
        registry_kind: str,
        registry_content_hash: str,
        lease_owner: str,
        fencing_token: int,
        release_manifest_id: int | None = None,
        capital_permission_manifest_id: int | None = None,
        for_update: bool = True,
    ) -> dict[str, Any] | None:
        """Load and lock the complete DB-authoritative chain-write boundary."""
        sql = """
            SELECT
              a.id AS account_id, a.account_key, a.status AS account_status,
              a.provider AS account_provider, a.chain_id AS account_chain_id,
              a.identity_type AS account_identity_type,
              a.network_mode AS account_network_mode,
              a.funder_address, a.maker_address, a.signing_identity,
              a.release_manifest_id AS account_release_manifest_id,
              a.capital_permission_manifest_id AS account_capital_permission_manifest_id,
              m.id AS market_id, m.condition_id AS market_condition_id,
              m.neg_risk AS market_neg_risk, m.closed AS market_closed,
              m.accepting_orders AS market_accepting_orders,
              rel.id AS release_manifest_id, rel.release_name,
              rel.total_hash AS release_total_hash, rel.status AS release_status,
              rel.db_revision AS release_db_revision,
              rel.capital_permission_manifest_id AS release_capital_permission_manifest_id,
              perm.id AS capital_permission_manifest_id, perm.name AS permission_name,
              perm.mode AS permission_mode, perm.capability AS permission_capability,
              perm.authorized_capital AS permission_authorized_capital,
              perm.kill_switch AS permission_kill_switch,
              perm.content_hash AS permission_content_hash,
              perm.status AS permission_status,
              lease.owner AS lease_owner, lease.fencing_token AS lease_fencing_token,
              lease.lease_until,
              reg.id AS registry_version_id, reg.registry_version,
              reg.kind AS registry_kind, reg.address AS registry_address,
              reg.proxy_kind AS registry_proxy_kind,
              reg.runtime_keccak AS registry_runtime_keccak,
              reg.resolved_implementation_or_beacon AS registry_resolved_implementation_or_beacon,
              reg.resolved_code_keccak AS registry_resolved_code_keccak,
              reg.extra AS registry_extra,
              reg.snapshot_block_number AS registry_snapshot_block_number,
              reg.snapshot_block_hash AS registry_snapshot_block_hash,
              reg.content_hash AS registry_content_hash,
              reg.status AS registry_status,
              bundle.registry_bundle, bundle.registry_bundle_content_hash,
              bundle.registry_bundle_entries,
              EXISTS (
                SELECT 1 FROM trading.account_reconciliations ar
                 WHERE ar.account_id=a.id AND (ar.status='RECONCILING' OR
                    (ar.status='FAILED' AND NOT EXISTS (
                       SELECT 1 FROM trading.account_reconciliations ok
                        WHERE ok.account_id=a.id AND ok.status='COMPLETED' AND ok.id>ar.id)))
              ) AS active_reconciliation
            FROM trading.pm_accounts a
            JOIN trading.pm_markets m ON m.id=:market_id
            JOIN trading.release_manifests rel ON rel.id=a.release_manifest_id
            JOIN trading.capital_permission_manifests perm
              ON perm.id=a.capital_permission_manifest_id
            JOIN trading.execution_leases lease
              ON lease.account_id=a.id AND lease.lease_role='EXECUTION'
             AND lease.owner=:lease_owner AND lease.fencing_token=:fencing_token
             AND lease.lease_until>statement_timestamp()
            JOIN trading.contract_registry reg
              ON reg.chain_id=a.chain_id AND reg.kind=:registry_kind
             AND reg.content_hash=:registry_hash AND reg.status='ACTIVE'
            JOIN LATERAL (
              SELECT material.registry_bundle,
                     encode(sha256(convert_to(material.registry_bundle::text,'UTF8')),'hex')
                       AS registry_bundle_content_hash,
                     material.registry_bundle_entries
                FROM (
                  SELECT jsonb_object_agg(rb.kind,rb.content_hash ORDER BY rb.kind)
                           AS registry_bundle,
                         jsonb_object_agg(
                           rb.kind,
                           jsonb_build_object(
                             'id',rb.id,'registry_version',rb.registry_version,
                             'version_no',rb.version_no,'address',rb.address,
                             'proxy_kind',rb.proxy_kind,'runtime_keccak',rb.runtime_keccak,
                             'resolved_implementation_or_beacon',rb.resolved_implementation_or_beacon,
                             'resolved_code_keccak',rb.resolved_code_keccak,
                             'snapshot_block_number',rb.snapshot_block_number,
                             'snapshot_block_hash',rb.snapshot_block_hash,
                             'content_hash',rb.content_hash,'extra',rb.extra)
                           ORDER BY rb.kind) AS registry_bundle_entries,
                         count(*) AS member_count
                    FROM trading.contract_registry rb
                   WHERE rb.chain_id=reg.chain_id AND rb.status='ACTIVE'
                     AND rb.registry_version=reg.registry_version
                     AND rb.version_no=reg.version_no
                     AND rb.snapshot_block_number=reg.snapshot_block_number
                     AND rb.snapshot_block_hash=reg.snapshot_block_hash
                     AND rb.kind IN ('pusd','ctf','deposit_wallet',:registry_kind)
                ) material WHERE material.member_count=4
            ) bundle ON true
            WHERE a.id=:account_id
              AND (CAST(:release_id AS bigint) IS NULL OR rel.id=CAST(:release_id AS bigint))
              AND (CAST(:permission_id AS bigint) IS NULL OR perm.id=CAST(:permission_id AS bigint))
        """
        if for_update:
            # Only mutable per-account authority owns the operation critical
            # section.  Locking shared market/release/permission/registry rows
            # made unrelated accounts deadlock under concurrent settlement;
            # their exact immutable/content-hash facts are rechecked again at
            # TX1 and immediately before egress.
            sql += " FOR UPDATE OF a, lease"
        result = await session.execute(
            text(sql),
            {
                "account_id": account_id,
                "market_id": market_id,
                "registry_kind": registry_kind,
                "registry_hash": registry_content_hash,
                "release_id": release_manifest_id,
                "permission_id": capital_permission_manifest_id,
                "lease_owner": lease_owner,
                "fencing_token": fencing_token,
            },
        )
        rows = _rows(result)
        return rows[0] if rows else None

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

    async def load_recovery_context(
        self,
        session: AsyncSession,
        *,
        operation_id: int,
        lease_owner: str,
        fencing_token: int,
        for_update: bool = True,
    ) -> dict[str, Any] | None:
        """Load only immutable operation facts plus the *current* recovery fence.

        Recovery intentionally does not join current release/permission/kill/reconciliation
        or active registry pointers: those gate new signing, not authoritative read-only
        reconciliation of an already-sent operation.  Frozen registry/bundle/artifact facts
        remain on the operation itself.
        """
        sql = (
            "SELECT o.*, a.signing_identity, l.owner AS current_lease_owner, "
            "l.fencing_token AS current_lease_fencing_token, l.lease_until "
            "FROM trading.chain_operations o "
            "JOIN trading.pm_accounts a ON a.id=o.account_id "
            "JOIN trading.execution_leases l ON l.account_id=o.account_id "
            " AND l.lease_role='EXECUTION' AND l.owner=:owner "
            " AND l.fencing_token=:fence AND l.lease_until>statement_timestamp() "
            "WHERE o.id=:oid AND :fence>=o.fencing_token"
        )
        if for_update:
            sql += " FOR UPDATE OF o, l"
        result = await session.execute(
            text(sql),
            {"oid": operation_id, "owner": lease_owner, "fence": fencing_token},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def update_evidence(
        self,
        session: AsyncSession,
        operation_id: int,
        fields: dict[str, Any],
        *,
        lease_owner: str,
        fencing_token: int,
    ) -> None:
        """Write evidence once under the exact current execution lease.

        A replay with identical values is a no-op; overwrites, stale owners and terminal
        mutation fail closed before any SQL UPDATE.
        """
        allowed = {
            "relayer_nonce", "deadline", "transaction_id", "transaction_hash",
            "receipt_block_number", "receipt_block_hash", "finalized_block_number",
            "receipt_status", "canonical_block_hash", "finalized_block_hash",
            "balance_evidence_artifact_id", "balance_evidence_hash",
            "pre_balance", "post_balance",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"chain_operation evidence columns not allowed: {sorted(unknown)}")
        if not fields:
            return
        current_result = await session.execute(
            text(
                "SELECT o.* FROM trading.chain_operations o "
                "JOIN trading.execution_leases l ON l.account_id=o.account_id "
                " AND l.lease_role='EXECUTION' AND l.owner=:owner "
                " AND l.fencing_token=:fence AND l.lease_until>statement_timestamp() "
                "WHERE o.id=:oid FOR UPDATE OF o, l"
            ),
            {"oid": operation_id, "owner": lease_owner, "fence": fencing_token},
        )
        rows = _rows(current_result)
        if not rows:
            raise RuntimeError("chain_operation_evidence_fence_invalid")
        current = rows[0]
        if current["status"] in {"FINALIZED", "INVALID", "FAILED", "SETTLEMENT_CONFLICT", "REVERSED"}:
            raise RuntimeError("chain_operation_evidence_terminal")
        changed: dict[str, Any] = {}
        for column, value in fields.items():
            if current[column] is not None:
                if current[column] != value:
                    raise RuntimeError(f"chain_operation_evidence_conflict:{column}")
            else:
                changed[column] = value
        if not changed:
            return
        assignments = ", ".join(f"{col} = :{col}" for col in changed)
        params = dict(changed)
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
                    event_type, event_payload, event_hash, lease_owner, fence_token
                ) VALUES (
                    :operation_id, :sequence_no, :transition_from, :transition_to,
                    :event_type, :event_payload, :event_hash, :lease_owner, :fence_token
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
                "lease_owner": event["lease_owner"],
                "fence_token": event["fence_token"],
            },
        )

    async def mark_economic_effect_applied(
        self,
        session: AsyncSession,
        *,
        operation_id: int,
        lease_owner: str,
        fencing_token: int,
    ) -> bool:
        """CAS the effect bit only after the unique settlement ledger is POSTED."""
        result = await session.execute(
            text(
                "UPDATE trading.chain_operations o SET economic_effect_applied=true, "
                "updated_at=now() FROM trading.execution_leases lease "
                "WHERE o.id=:oid AND o.status='FINALIZED' "
                "AND NOT o.economic_effect_applied "
                "AND lease.account_id=o.account_id AND lease.lease_role='EXECUTION' "
                "AND lease.owner=:owner AND lease.fencing_token=:fence "
                "AND lease.lease_until>statement_timestamp() "
                "AND EXISTS (SELECT 1 FROM trading.ledger_transactions tx "
                " WHERE tx.chain_operation_id=o.id AND tx.kind='SETTLEMENT' "
                " AND tx.status='POSTED')"
            ),
            {"oid": operation_id, "owner": lease_owner, "fence": fencing_token},
        )
        return result.rowcount == 1

    async def next_sequence(self, session: AsyncSession, operation_id: int) -> int:
        await session.execute(
            text("SELECT id FROM trading.chain_operations WHERE id=:oid FOR UPDATE"),
            {"oid": operation_id},
        )
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
                 WHERE status IN ('PREPARED','SUBMITTING','UNKNOWN','RELAYER_NEW','EXECUTED','MINED',
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
        result = await session.execute(
                text(
                    """
                    INSERT INTO trading.idempotency_claims (scope, key, owner)
                    VALUES ('chain_operation', :key, :owner)
                    ON CONFLICT (scope, key) DO NOTHING
                    RETURNING id
                    """
                ),
                {"key": key, "owner": owner},
            )
        if result.scalar_one_or_none() is not None:
            return True
        existing = (
            await session.execute(
                text(
                    "SELECT owner FROM trading.idempotency_claims "
                    "WHERE scope='chain_operation' AND key=:key FOR UPDATE"
                ),
                {"key": key},
            )
        ).scalar_one()
        if existing != owner:
            raise RuntimeError("chain_operation_idempotency_owner_conflict")
        return False


class SettlementObservationRepository:
    """settlement_observations append-only；COMPLETE 五元组由 deferred trigger 核验。"""

    async def insert_observation(self, session: AsyncSession, row: dict[str, Any]) -> int:
        """Append one observation; exact retries return the existing identity.

        ``observation_key`` is the idempotency identity.  A replay must match every
        frozen fact, not merely its caller-supplied content hash.  Different content
        under the same key is a hard conflict while leaving the transaction usable.
        """
        params = {
            "observation_key": row["observation_key"],
            "settlement_set_key": row["settlement_set_key"],
            "source_kind": row["source_kind"],
            "condition_id": row["condition_id"],
            "market_id": row["market_id"],
            "token_set": json.dumps(row["token_set"]),
            "token_set_hash": row["token_set_hash"],
            "payout_vector": (
                json.dumps(row.get("payout_vector"))
                if row.get("payout_vector") is not None else None
            ),
            "outcome_index": row.get("outcome_index"),
            "numerator": row.get("numerator"),
            "denominator": row.get("denominator"),
            "winner": row.get("winner"),
            "is_50_50_outcome": row.get("is_50_50_outcome"),
            "redeemable": row.get("redeemable"),
            "label_audit_version": row.get("label_audit_version"),
            "source_version": row["source_version"],
            "source_cutoff": row["source_cutoff"],
            "as_of": row["as_of"],
            "received_at": row["received_at"],
            "raw_artifact_ref": row.get("raw_artifact_ref"),
            "raw_artifact_id": row.get("raw_artifact_id"),
            "raw_artifact_hash": row["raw_artifact_hash"],
            "content_hash": row["content_hash"],
            "payload": (
                json.dumps(row.get("payload"))
                if row.get("payload") is not None else None
            ),
            "status": row.get("status", "PENDING"),
        }
        result = await session.execute(
            text(
                """
                INSERT INTO trading.settlement_observations (
                    observation_key, settlement_set_key, source_kind, condition_id,
                    market_id, token_set, token_set_hash, payout_vector,
                    outcome_index, numerator, denominator, winner, is_50_50_outcome,
                    redeemable, label_audit_version, source_version, source_cutoff,
                    as_of, received_at, raw_artifact_ref, raw_artifact_id,
                    raw_artifact_hash, content_hash, payload, status
                ) VALUES (
                    :observation_key, :settlement_set_key, :source_kind, :condition_id,
                    :market_id, CAST(:token_set AS jsonb), :token_set_hash,
                    CAST(:payout_vector AS jsonb), :outcome_index, :numerator, :denominator,
                    :winner, :is_50_50_outcome, :redeemable, :label_audit_version,
                    :source_version, :source_cutoff, :as_of, :received_at,
                    :raw_artifact_ref, :raw_artifact_id, :raw_artifact_hash,
                    :content_hash, CAST(:payload AS jsonb), :status
                )
                ON CONFLICT (observation_key) DO NOTHING
                RETURNING id
                """
            ),
            params,
        )
        inserted = result.scalar_one_or_none()
        if inserted is not None:
            return int(inserted)
        exact = await session.execute(
            text(
                """
                SELECT id FROM trading.settlement_observations
                 WHERE observation_key=:observation_key
                   AND settlement_set_key=:settlement_set_key
                   AND source_kind=:source_kind AND condition_id=:condition_id
                   AND market_id=:market_id
                   AND token_set=CAST(:token_set AS jsonb)
                   AND token_set_hash=:token_set_hash
                   AND payout_vector IS NOT DISTINCT FROM CAST(:payout_vector AS jsonb)
                   AND outcome_index IS NOT DISTINCT FROM :outcome_index
                   AND numerator IS NOT DISTINCT FROM :numerator
                   AND denominator IS NOT DISTINCT FROM :denominator
                   AND winner IS NOT DISTINCT FROM :winner
                   AND is_50_50_outcome IS NOT DISTINCT FROM :is_50_50_outcome
                   AND redeemable IS NOT DISTINCT FROM :redeemable
                   AND label_audit_version IS NOT DISTINCT FROM :label_audit_version
                   AND source_version=:source_version AND source_cutoff=:source_cutoff
                   AND as_of=:as_of AND received_at=:received_at
                   AND raw_artifact_ref IS NOT DISTINCT FROM :raw_artifact_ref
                   AND raw_artifact_id IS NOT DISTINCT FROM :raw_artifact_id
                   AND raw_artifact_hash=:raw_artifact_hash
                   AND content_hash=:content_hash
                   AND payload IS NOT DISTINCT FROM CAST(:payload AS jsonb)
                   AND status=:status
                 FOR SHARE
                """
            ),
            params,
        )
        existing = exact.scalar_one_or_none()
        if existing is None:
            raise RuntimeError("settlement_observation_idempotency_conflict")
        return int(existing)

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

    async def get_complete_set(
        self,
        session: AsyncSession,
        *,
        condition_id: str,
        market_id: int,
        settlement_set_key: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return one exact immutable five-source set; ambiguity fails closed."""
        key = settlement_set_key
        if key is None:
            candidates = await session.execute(
                text(
                    "SELECT settlement_set_key FROM trading.settlement_observations "
                    "WHERE condition_id=:condition AND market_id=:market AND status='COMPLETE' "
                    "GROUP BY settlement_set_key HAVING count(*)=5 "
                    "ORDER BY max(source_cutoff) DESC, settlement_set_key DESC LIMIT 2"
                ),
                {"condition": condition_id, "market": market_id},
            )
            keys = [row[0] for row in candidates.fetchall()]
            if not keys:
                return []
            # Equal cutoffs are not silently tie-broken: caller must bind the set key.
            if len(keys) > 1:
                cutoffs = await session.execute(
                    text(
                        "SELECT settlement_set_key,max(source_cutoff) FROM "
                        "trading.settlement_observations WHERE settlement_set_key=ANY(:keys) "
                        "GROUP BY settlement_set_key"
                    ),
                    {"keys": keys},
                )
                material = cutoffs.fetchall()
                if len({row[1] for row in material}) != len(material):
                    raise RuntimeError("settlement_observation_set_ambiguous")
            key = keys[0]
        result = await session.execute(
            text(
                "SELECT * FROM trading.settlement_observations "
                "WHERE settlement_set_key=:key AND condition_id=:condition "
                "AND market_id=:market AND status='COMPLETE' ORDER BY source_kind"
            ),
            {"key": key, "condition": condition_id, "market": market_id},
        )
        rows = _rows(result)
        if len(rows) != 5:
            return []
        return rows
