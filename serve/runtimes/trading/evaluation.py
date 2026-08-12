"""P3 evaluation runtime（WP-04 Checkpoint C）。

P3 独立 pool：不 import 也不复用 execution worker；不直接更新 strategy/permission/历史
事实。每个 Handler 只做一次 UoW；外部/长计算不持有 DB transaction。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from app.db.uow import UnitOfWork
from app.handlers.trading.evaluation import EvaluationEvent, EvaluationHandler
from app.handlers.trading.settlement import SettlementEvent, SettlementHandler
from app.logics.trading.evaluation import EvaluationLogic
from app.domain.trading.hashing import canonical_bytes, canonical_hash
from app.logics.trading.settlement import ChainSettlementLogic, SettlementLogic
from app.repositories.trading.evaluation import EvaluationRepository
from app.repositories.trading.settlement import SettlementRepository
from app.repositories.trading.market_stream import MarketStreamRepository
from app.schemas.trading.settlement import (
    ChainRecoveryEvidence,
    ChainRedeemRequest,
    ChainSettlementEvidenceInput,
    ChainWireEvidence,
)
from app.services.artifact_store import ArtifactStore

logger = logging.getLogger(__name__)


class EvaluationRuntime:
    """settlement / evaluation 编排（每步一次 UoW；P3 独立 pool）。"""

    def __init__(
        self,
        sessions_factory: Any,
        artifact_store: ArtifactStore | None = None,
        *,
        polygon_driver: Any | None = None,
        relayer_driver: Any | None = None,
        geoblock_driver: Any | None = None,
        runtime_identity: str | None = None,
        registry_content_hashes: dict[str, str] | None = None,
    ) -> None:
        self._sessions = sessions_factory
        self._settlement_logic = SettlementLogic(
            SettlementRepository(), artifact_store=artifact_store
        )
        self._evaluation_logic = EvaluationLogic(
            EvaluationRepository(), SettlementRepository()
        )
        self._chain_logic = ChainSettlementLogic()
        self._settlement_handler = SettlementHandler(
            self._settlement_logic, chain_logic=self._chain_logic
        )
        self._evaluation_handler = EvaluationHandler(self._evaluation_logic)
        self._polygon = polygon_driver
        self._relayer = relayer_driver
        self._geoblock = geoblock_driver
        self._artifact_store = artifact_store
        self._artifact_catalog = MarketStreamRepository()
        self._runtime_identity = runtime_identity
        self._registry_hashes = dict(registry_content_hashes or {})

    async def handle_settlement_event(self, event: SettlementEvent) -> Any:
        async with UnitOfWork(self._sessions) as uow:
            return await self._settlement_handler.handle(uow, event)

    async def handle_evaluation_event(self, event: EvaluationEvent) -> Any:
        async with UnitOfWork(self._sessions) as uow:
            return await self._evaluation_handler.handle(uow, event)

    async def record_chain_settlement_evidence(
        self, evidence: ChainSettlementEvidenceInput
    ) -> str:
        """Production observation entry: typed evidence -> DB validation -> exact set."""
        async with UnitOfWork(self._sessions) as uow:
            return await self._chain_logic.record_settlement_evidence(
                uow, evidence=evidence
            )

    async def submit_redeem(self, request: ChainRedeemRequest) -> dict[str, Any]:
        """DB preflight ×2 -> read/sign -> TX1 -> one submit -> TX2.

        Every driver call is visibly outside a :class:`UnitOfWork`.  A timeout or
        cancellation after the write boundary converges to UNKNOWN and is never
        submitted again by this method or by recovery.
        """
        polygon, relayer, geoblock, identity = self._chain_dependencies()
        async with UnitOfWork(self._sessions) as uow:
            existing = await self._chain_logic.chain_operations.get_by_key(
                uow.session, request.operation_key
            )
            if existing is not None:
                exact = {
                    "idempotency_key": request.idempotency_key,
                    "account_id": request.account_id,
                    "market_id": request.market_id,
                    "condition_id": request.condition_id.lower(),
                }
                for field, expected in exact.items():
                    actual = existing[field]
                    if field == "condition_id":
                        actual = str(actual).lower()
                    if actual != expected:
                        raise RuntimeError(f"chain_existing_operation_mismatch:{field}")
                await self._chain_logic._load_owned_operation(
                    uow,
                    int(existing["id"]),
                    runtime_identity=identity,
                    fencing_token=request.fencing_token,
                )
                return {
                    "operation_id": int(existing["id"]),
                    "status": existing["status"],
                    "replayed": True,
                    "recovery_required": existing["status"] not in {
                        "FINALIZED", "INVALID", "FAILED", "SETTLEMENT_CONFLICT", "REVERSED"
                    },
                }
        kind, registry_hash = await self._trusted_registry_for(request.market_id)
        async with UnitOfWork(self._sessions) as uow:
            first = await self._chain_logic.preflight_redeem(
                uow, request=request, runtime_identity=identity,
                expected_registry_content_hash=registry_hash,
            )
        async with UnitOfWork(self._sessions) as uow:
            second = await self._chain_logic.preflight_redeem(
                uow, request=request, runtime_identity=identity,
                expected_registry_content_hash=registry_hash,
            )
        if first.authority_hash != second.authority_hash or first.registry_kind != kind:
            raise RuntimeError("chain_preflight_repeat_mismatch")
        # Geoblock is a typed provider fact, never a caller boolean. It is read and
        # freshness-checked before nonce acquisition/signing, then bound by TX1.
        try:
            geo = await geoblock.check()
        except Exception as exc:
            from app.services.polymarket.geoblock_driver import GeoblockCheckError
            if not isinstance(exc, GeoblockCheckError):
                raise
            geo_ref = await self._put_chain_artifact(exc.artifact_material)
            async with UnitOfWork(self._sessions) as uow:
                await self._artifact_catalog.register_artifact(uow.session, geo_ref)
                await self._chain_logic.audit.insert_external_call_attempt(
                    uow.session,
                    attempt_key=(
                        f"chain:{request.operation_key}:geoblock:check:"
                        f"rejected:{geo_ref.sha256[:12]}"
                    ),
                    driver="geoblock",
                    endpoint="check",
                    method="GET",
                    request_hash=canonical_hash({
                        "operation_key": request.operation_key,
                        "account_id": request.account_id,
                        "market_id": request.market_id,
                    }),
                    response_hash=geo_ref.sha256,
                    status_code=None,
                    latency_ms=0,
                    rate_limit_remaining=None,
                    error_reason=exc.reason_code,
                    fence_token=request.fencing_token,
                )
            raise RuntimeError(exc.reason_code) from None
        geo_ref = await self._put_chain_artifact(geo.artifact_material())
        self._assert_registry_bundle(first.registry_bundle)
        registry_proof = await self._verify_registry_bundle(
            polygon, first.registry_bundle
        )
        registry_ref = await self._put_chain_artifact({
            "schema": "chain-registry-verification/v1",
            "registry_bundle_content_hash": first.registry_bundle_hash,
            "entries": registry_proof,
        })
        pre_balance = await self._read_balances(
            polygon,
            wallet=first.wallet_address,
            token_set=first.token_set,
            pusd_address=first.pusd_address,
            ctf_address=first.ctf_address,
        )
        pre_balance["contracts"] = {
            "pusd": first.pusd_address.lower(),
            "ctf": first.ctf_address.lower(),
            "deposit_wallet": first.deposit_wallet_address.lower(),
            "adapter": first.registry_address.lower(),
            "registry_bundle_hash": first.registry_bundle_hash,
        }
        approved = await polygon.erc1155_is_approved_for_all(
            first.ctf_address, first.wallet_address, first.registry_address,
            block_tag=pre_balance["block_number_hex"],
        )
        if approved is not True:
            raise RuntimeError("chain_redeem_operator_approval_missing")
        opaque = await relayer.prepare_batch(
            from_address=first.signing_identity,
            to_address=first.deposit_wallet_address,
            deposit_wallet=first.wallet_address,
            calls=list(first.calls),
            metadata="pm-v2-settlement/v1",
        )
        async with UnitOfWork(self._sessions) as uow:
            # Serialize the deterministic operation key before any row locks.
            # The same lock is acquired by the post-commit pre-send check below,
            # preventing a duplicate TX1 from holding account while the transport
            # owner holds operation/lease (the former deadlock cycle).
            await uow.session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                {"key": f"chain-operation:{request.operation_key}"},
            )
            registry_artifact_id = await self._artifact_catalog.register_artifact(
                uow.session, registry_ref
            )
            geo_artifact_id = await self._artifact_catalog.register_artifact(
                uow.session, geo_ref
            )
            wire = ChainWireEvidence(
                nonce=opaque.nonce,
                deadline=datetime.fromtimestamp(opaque.deadline, tz=timezone.utc),
                body_hash=opaque.body_hash,
                call_set_hash=canonical_hash(list(first.calls)),
                pre_balance=pre_balance,
                registry_content_hash=first.registry_content_hash,
                registry_bundle={
                    str(row["kind"]): str(row["content_hash"])
                    for row in first.registry_bundle
                },
                registry_bundle_content_hash=first.registry_bundle_hash,
                registry_evidence_hash=registry_ref.sha256,
                registry_evidence_artifact_id=registry_artifact_id,
                geo_evidence_hash=geo_ref.sha256,
                geo_evidence_artifact_id=geo_artifact_id,
                geo_allowed=geo.allowed,
                geo_observed_at=geo.observed_at,
                geo_source_version=geo.source_version,
                settlement_set_key=first.settlement_set_key,
            )
            prepared = await self._chain_logic.prepare_redeem(
                uow, request=request, runtime_identity=identity,
                expected_registry_content_hash=registry_hash,
                first_preflight_hash=first.authority_hash, wire=wire,
            )
            await self._record_external_attempts(
                uow,
                operation_key=request.operation_key,
                fencing_token=request.fencing_token,
                attempts=(
                    ("geoblock", "check", "GET", canonical_hash({"operation_key": request.operation_key}), geo_ref.sha256),
                    ("polygon", "registry_bundle", "GET", first.registry_bundle_hash, registry_ref.sha256),
                    ("polygon", "pre_balances", "GET", canonical_hash({"wallet": first.wallet_address, "tokens": first.token_set}), canonical_hash(pre_balance)),
                    ("relayer", "prepare_batch", "GET", canonical_hash({"signer": first.signing_identity, "calls": first.calls}), opaque.body_hash),
                ),
            )
        if not prepared.transport_owner:
            return {
                "operation_id": prepared.operation_id,
                "status": "SUBMITTING",
                "replayed": True,
                "recovery_required": True,
            }
        # Last-moment post-commit fence/body check.  A takeover after TX1 is a
        # hard stop before the only write boundary.
        async with UnitOfWork(self._sessions) as uow:
            await uow.session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                {"key": f"chain-operation:{request.operation_key}"},
            )
            persisted = await self._chain_logic._load_owned_operation(
                uow, prepared.operation_id, runtime_identity=identity,
                fencing_token=request.fencing_token,
            )
            presend = await self._chain_logic.preflight_redeem(
                uow,
                request=request,
                runtime_identity=identity,
                expected_registry_content_hash=registry_hash,
            )
            if persisted["status"] != "SUBMITTING" or persisted["body_hash"] != opaque.body_hash:
                raise RuntimeError("chain_presend_binding_drift")
            if presend.authority_hash != first.authority_hash:
                raise RuntimeError("chain_presend_authority_drift")
            if (
                persisted.get("geo_allowed") is not True
                or persisted.get("geo_evidence_artifact_id") != geo_artifact_id
                or persisted.get("geo_evidence_hash") != geo_ref.sha256
            ):
                raise RuntimeError("chain_presend_geo_binding_drift")
            observed_at = persisted.get("geo_observed_at")
            now = datetime.now(timezone.utc)
            if observed_at is None or observed_at > now or (now - observed_at).total_seconds() > 30:
                raise RuntimeError("chain_presend_geo_evidence_stale")
        try:
            outcome = await relayer.submit_prepared(opaque)
        except asyncio.CancelledError:
            # Cancellation may arrive after bytes crossed the write boundary.  A
            # shielded fresh task records UNKNOWN before propagating cancellation;
            # recovery will query status/nonce/receipt and never re-submit.
            await asyncio.shield(self._persist_unknown_submit(
                operation_id=prepared.operation_id,
                fencing_token=request.fencing_token,
                body_hash=opaque.body_hash,
            ))
            raise
        except Exception as exc:
            from app.schemas.polymarket.common import PolymarketError
            if not isinstance(exc, PolymarketError):
                raise
            from app.services.polymarket.relayer_driver import SubmitOutcome
            logger.info("chain submit outcome unknown", extra={"reason": type(exc).__name__})
            outcome = SubmitOutcome(cls="UNKNOWN")
        async with UnitOfWork(self._sessions) as uow:
            await self._record_external_attempts(
                uow,
                operation_key=request.operation_key,
                fencing_token=request.fencing_token,
                attempts=((
                    "relayer", "submit", "POST", opaque.body_hash,
                    canonical_hash({
                        "class": outcome.cls,
                        "transaction_id": outcome.transaction_id,
                        "transaction_hash": outcome.transaction_hash,
                        "state": outcome.state,
                    }),
                ),),
            )
            applied = await self._chain_logic.apply_submit_outcome(
                uow, operation_id=prepared.operation_id,
                runtime_identity=identity, fencing_token=request.fencing_token,
                outcome=outcome, sent_body_hash=opaque.body_hash,
            )
        return {"operation_id": prepared.operation_id, **applied}

    async def _persist_unknown_submit(
        self, *, operation_id: int, fencing_token: int, body_hash: str
    ) -> None:
        from app.services.polymarket.relayer_driver import SubmitOutcome

        _, _, _, identity = self._chain_dependencies()
        async with UnitOfWork(self._sessions) as uow:
            await self._chain_logic.apply_submit_outcome(
                uow,
                operation_id=operation_id,
                runtime_identity=identity,
                fencing_token=fencing_token,
                outcome=SubmitOutcome(cls="UNKNOWN"),
                sent_body_hash=body_hash,
            )

    async def recover_chain_operation(
        self, *, operation_id: int, fencing_token: int, audit_finalized: bool = False
    ) -> dict[str, Any]:
        """Read-only provider recovery; never creates/signs/submits a batch."""
        polygon, relayer, _, identity = self._chain_dependencies()
        async with UnitOfWork(self._sessions) as uow:
            plan = await self._chain_logic.recover_unknown(
                uow, operation_id, runtime_identity=identity,
                fencing_token=fencing_token,
                allow_finalized_audit=audit_finalized,
            )
        relayer_state = None
        transaction_hash = plan.get("transaction_hash")
        transaction_id = plan.get("transaction_id")
        if transaction_id:
            remote = await relayer.get_transaction_status(transaction_id)
            relayer_state = remote.normalized_state
            transaction_hash = remote.transaction_hash or transaction_hash
        # Nonce is queried even when the submit response was lost; it is evidence only.
        nonce = await relayer.get_nonce(plan["signing_identity"])
        # Registry and all runtime contract identities are re-established on every
        # recovery/finality pass.  The proof bytes must be identical to TX1's frozen
        # artifact; an active-row or provider-code drift hard-stops the operation.
        frozen_bundle = dict(plan.get("registry_bundle") or {})
        adapter_kinds = set(frozen_bundle) - {"pusd", "ctf", "deposit_wallet"}
        if len(adapter_kinds) != 1:
            raise RuntimeError("chain_recovery_frozen_registry_bundle_invalid")
        kind = next(iter(adapter_kinds))
        async with UnitOfWork(self._sessions) as uow:
            rows = (
                await uow.session.execute(
                    text(
                        "SELECT id,registry_version,kind,version_no,chain_id,address,"
                        "proxy_kind,runtime_keccak,resolved_implementation_or_beacon,"
                        "resolved_code_keccak,snapshot_block_number,snapshot_block_hash,"
                        "content_hash,extra,status FROM trading.contract_registry "
                        "WHERE chain_id=137 AND kind=ANY(:kinds) AND content_hash=ANY(:hashes) "
                        "ORDER BY kind"
                    ),
                    {"kinds": list(frozen_bundle), "hashes": list(frozen_bundle.values())},
                )
            ).mappings().all()
        bundle = [dict(row) for row in rows]
        if {row["kind"]: row["content_hash"] for row in bundle} != frozen_bundle:
            raise RuntimeError("chain_recovery_registry_context_missing")
        bundle_tuple = tuple(bundle)
        self._assert_registry_bundle(bundle_tuple, enforce_deployment=False)
        registry_proof = await self._verify_registry_bundle(polygon, bundle_tuple)
        registry_ref = await self._put_chain_artifact({
            "schema": "chain-registry-verification/v1",
            "registry_bundle_content_hash": plan["registry_bundle_content_hash"],
            "entries": registry_proof,
        })
        if registry_ref.sha256 != plan["registry_evidence_hash"]:
            raise RuntimeError("chain_recovery_registry_evidence_drift")
        by_kind = {str(row["kind"]): row for row in bundle}

        receipt = None
        canonical_block = None
        finalized_block = None
        canonical: bool | None = None
        if transaction_hash:
            receipt = await polygon.eth_get_transaction_receipt(
                transaction_hash, consensus=True
            )
            if receipt is not None:
                if receipt.has_removed_log:
                    canonical = False
                else:
                    canonical_block = await polygon.eth_get_block_by_number(
                        receipt.block_number, consensus=True
                    )
                    canonical = bool(
                        canonical_block is not None
                        and canonical_block.hash == receipt.block_hash
                    )
                if receipt.success and canonical is True:
                    finalized_block = await polygon.eth_get_block_by_number(
                        "finalized", consensus=True
                    )
                    if finalized_block is None:
                        raise RuntimeError("chain_recovery_finalized_block_missing")

        finalized_after_receipt = bool(
            receipt is not None
            and finalized_block is not None
            and finalized_block.number_int > receipt.block_number_int
        )
        post_balance = None
        balance_ref = None
        if finalized_after_receipt:
            post_balance = await self._read_balances_for_market(
                polygon,
                wallet=plan["wallet_address"],
                market_id=plan["market_id"],
                pusd_address=by_kind["pusd"]["address"],
                ctf_address=by_kind["ctf"]["address"],
                finalized_block=finalized_block,
            )
            post_balance["contracts"] = {
                "pusd": by_kind["pusd"]["address"].lower(),
                "ctf": by_kind["ctf"]["address"].lower(),
                "deposit_wallet": by_kind["deposit_wallet"]["address"].lower(),
                "adapter": by_kind[kind]["address"].lower(),
                "registry_bundle_hash": plan["registry_bundle_content_hash"],
            }

        material = {
            "schema": "chain-recovery-evidence/v1",
            "operation_key": plan["operation_key"],
            "relayer_state": relayer_state,
            "transaction_id": transaction_id,
            "transaction_hash": transaction_hash,
            "nonce": nonce,
            "receipt_block_number": receipt.block_number_int if receipt else None,
            "receipt_block_hash": receipt.block_hash if receipt else None,
            "canonical_block_hash": canonical_block.hash if canonical_block else None,
            "finalized_block_number": finalized_block.number_int if finalized_block else None,
            "finalized_block_hash": finalized_block.hash if finalized_block else None,
            "canonical": canonical,
            "receipt_success": receipt.success if receipt else None,
            "receipt_removed": receipt.has_removed_log if receipt else False,
            "finalized_after_receipt": finalized_after_receipt,
            "post_balance": post_balance,
            "registry_evidence_hash": registry_ref.sha256,
        }
        # Every recovery result, including UNKNOWN/failed/reorg/provisional, is a
        # durable raw provider artifact. A finalized balance proof reuses this exact
        # artifact as the operation's immutable balance evidence.
        provider_ref = await self._put_chain_artifact(material)
        if post_balance is not None:
            balance_ref = provider_ref
        async with UnitOfWork(self._sessions) as uow:
            provider_artifact_id = await self._artifact_catalog.register_artifact(
                uow.session, provider_ref
            )
            balance_artifact_id = provider_artifact_id if balance_ref is not None else None
            evidence_material = {
                key: value for key, value in material.items()
                if key not in {"schema", "operation_key", "registry_evidence_hash"}
            }
            evidence = ChainRecoveryEvidence(
                **evidence_material,
                balance_artifact_hash=balance_ref.sha256 if balance_ref else None,
                balance_artifact_id=balance_artifact_id,
                provider_artifact_hash=provider_ref.sha256,
                provider_artifact_id=provider_artifact_id,
                observation_hash=canonical_hash(material),
            )
            await self._record_external_attempts(
                uow,
                operation_key=plan["operation_key"],
                fencing_token=fencing_token,
                attempts=(
                    ("relayer", "status_nonce", "GET", canonical_hash({"transaction_id": transaction_id, "signer": plan["signing_identity"]}), canonical_hash({"state": relayer_state, "nonce": nonce, "transaction_hash": transaction_hash})),
                    ("polygon", "registry_bundle", "GET", plan["registry_bundle_content_hash"], registry_ref.sha256),
                    ("polygon", "receipt_finality_balance", "GET", canonical_hash({"transaction_hash": transaction_hash}), provider_ref.sha256),
                ),
            )
            return await self._chain_logic.apply_recovery(
                uow, operation_id=operation_id, runtime_identity=identity,
                fencing_token=fencing_token, evidence=evidence,
            )

    async def recover_chain_operations(
        self, *, limit: int = 200, include_finalized_audit: bool = False
    ) -> list[dict[str, Any]]:
        """Startup scheduler for persisted active operations, with zero resend path."""
        _, _, _, identity = self._chain_dependencies()
        async with UnitOfWork(self._sessions) as uow:
            rows = (
                await uow.session.execute(
                    text(
                        "SELECT o.id, lease.fencing_token FROM trading.chain_operations o "
                        "JOIN trading.execution_leases lease ON lease.account_id=o.account_id "
                        "AND lease.lease_role='EXECUTION' AND lease.owner=:owner "
                        "AND lease.lease_until>statement_timestamp() "
                        "WHERE o.status = ANY(:states) "
                        "ORDER BY o.id LIMIT :limit"
                    ),
                    {"owner": identity, "limit": limit, "states": [
                        "PREPARED", "SUBMITTING", "UNKNOWN", "RELAYER_NEW", "EXECUTED",
                        "MINED", "RELAYER_CONFIRMED", "MINED_PROVISIONAL", "REORGED",
                        *(["FINALIZED"] if include_finalized_audit else []),
                    ]},
                )
            ).mappings().all()
        results = []
        for row in rows:
            results.append(await self.recover_chain_operation(
                operation_id=int(row["id"]), fencing_token=int(row["fencing_token"])
                , audit_finalized=include_finalized_audit
            ))
        return results

    def _chain_dependencies(self) -> tuple[Any, Any, Any, str]:
        if (
            self._polygon is None
            or self._relayer is None
            or self._geoblock is None
            or self._artifact_store is None
            or not self._runtime_identity
        ):
            raise RuntimeError("chain_runtime_dependencies_missing")
        if (
            self._polygon.fixture_only is not True
            or self._relayer.fixture_only is not True
            or self._geoblock.fixture_only is not True
        ):
            raise RuntimeError("chain_runtime_fixture_capability_required")
        return self._polygon, self._relayer, self._geoblock, self._runtime_identity

    def _assert_registry_bundle(
        self, bundle: tuple[dict[str, Any], ...], *, enforce_deployment: bool = True
    ) -> None:
        for row in bundle:
            expected = self._registry_hashes.get(str(row["kind"]))
            if enforce_deployment and (expected is None or expected != row["content_hash"]):
                raise RuntimeError(f"chain_registry_frozen_hash_mismatch:{row['kind']}")

    @staticmethod
    def _registry_entry(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "address": row["address"],
            "chain_id": row["chain_id"],
            "proxy_kind": row["proxy_kind"],
            "runtime_keccak": row["runtime_keccak"],
            "resolved_implementation_or_beacon": row["resolved_implementation_or_beacon"],
            "resolved_code_keccak": row["resolved_code_keccak"],
            "snapshot_block_number": row["snapshot_block_number"],
            "snapshot_block_hash": row["snapshot_block_hash"],
            "extra": row.get("extra") or {},
        }

    async def _verify_registry_bundle(
        self, polygon: Any, bundle: tuple[dict[str, Any], ...]
    ) -> list[dict[str, Any]]:
        # Code/storage equality at a known block does not identify the network:
        # an RPC pointed at another chain could otherwise satisfy a fully mocked
        # snapshot.  Establish three-origin Polygon identity before verifying any
        # contract and bind it into the registry evidence artifact.
        provider_chain_id = await polygon.eth_chain_id(consensus=True)
        proof: list[dict[str, Any]] = []
        for row in bundle:
            entry = self._registry_entry(row)
            result = await polygon.verify_registry_entry(entry)
            if result.get("runtime_keccak") != row["runtime_keccak"]:
                raise RuntimeError("chain_registry_runtime_verification_mismatch")
            proof.append({
                "kind": row["kind"],
                "content_hash": row["content_hash"],
                "provider_chain_id": provider_chain_id,
                "entry": entry,
                "provider_evidence": result,
            })
        return proof

    async def _put_chain_artifact(self, material: dict[str, Any]):
        if self._artifact_store is None:
            raise RuntimeError("chain_artifact_store_missing")
        return await asyncio.to_thread(
            self._artifact_store.put_bytes,
            canonical_bytes(material),
            "application/json",
            "none",
        )

    async def _record_external_attempts(
        self,
        uow: UnitOfWork,
        *,
        operation_key: str,
        fencing_token: int,
        attempts: tuple[tuple[str, str, str, str, str], ...],
    ) -> None:
        """Append redacted, deterministic lineage for already-completed calls."""
        for driver, endpoint, method, request_hash, response_hash in attempts:
            await self._chain_logic.audit.insert_external_call_attempt(
                uow.session,
                attempt_key=(
                    f"chain:{operation_key}:{driver}:{endpoint}:"
                    f"{request_hash[:12]}:{response_hash[:12]}"
                ),
                driver=driver,
                endpoint=endpoint,
                method=method,
                request_hash=request_hash,
                response_hash=response_hash,
                status_code=200,
                latency_ms=0,
                rate_limit_remaining=None,
                error_reason=None,
                fence_token=fencing_token,
            )

    async def _trusted_registry_for(self, market_id: int) -> tuple[str, str]:
        async with UnitOfWork(self._sessions) as uow:
            kind = await self._chain_logic._registry_kind_for_market(uow, market_id)
        value = self._registry_hashes.get(kind)
        if not value or len(value) != 64:
            raise RuntimeError(f"chain_registry_frozen_hash_missing:{kind}")
        return kind, value

    async def _read_balances(
        self,
        polygon: Any,
        *,
        wallet: str,
        token_set: tuple[str, ...],
        pusd_address: str,
        ctf_address: str,
        finalized_block: Any | None = None,
    ) -> dict[str, Any]:
        finalized = finalized_block or await polygon.eth_get_block_by_number(
            "finalized", consensus=True
        )
        if finalized is None:
            raise RuntimeError("chain_balance_finalized_block_missing")
        block_tag = finalized.number
        pusd = await polygon.erc20_balance_of(
            pusd_address, wallet, block_tag=block_tag
        )
        tokens = {}
        for token_id in token_set:
            tokens[token_id.lower()] = await polygon.erc1155_balance_of(
                ctf_address, wallet, token_id, block_tag=block_tag
            )
        return {
            "block_number": finalized.number_int,
            "block_number_hex": finalized.number,
            "block_hash": finalized.hash,
            "pusd": str(pusd),
            "tokens": {key: str(value) for key, value in tokens.items()},
        }

    async def _read_balances_for_market(
        self,
        polygon: Any,
        *,
        wallet: str,
        market_id: int,
        pusd_address: str,
        ctf_address: str,
        finalized_block: Any,
    ) -> dict[str, Any]:
        async with UnitOfWork(self._sessions) as uow:
            rows = (await uow.session.execute(
                __import__("sqlalchemy").text(
                    "SELECT token_id FROM trading.pm_tokens WHERE market_id=:market "
                    "ORDER BY outcome_index"
                ), {"market": market_id}
            )).scalars().all()
        if len(rows) != 2:
            raise RuntimeError("chain_recovery_market_tokens_incomplete")
        return await self._read_balances(
            polygon,
            wallet=wallet,
            token_set=tuple(str(row).lower() for row in rows),
            pusd_address=pusd_address,
            ctf_address=ctf_address,
            finalized_block=finalized_block,
        )
