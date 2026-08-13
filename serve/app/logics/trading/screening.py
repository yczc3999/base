"""Frozen G0 context, hydrated-frame enrollment, deterministic R0 and audit."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN

from pydantic import ValidationError

from app.db.uow import UnitOfWork
from app.domain.trading.hashing import canonical_hash, deterministic_sample
from app.repositories.trading.cohort import CohortRepository, REQUIRED_COHORT_POLICIES
from app.repositories.trading.workflow import WorkflowRepository
from app.schemas.trading.workflow import (
    G0ObjectiveInput,
    HydratedUniverseFrameInput,
    R0BatchItemInput,
    R0Input,
    R0PolicyInput,
    RejectAuditPolicyInput,
)

G0_PASS = "PASS"
G0_FAIL = "PREDICTION_RESEARCH_ONLY"
G0_REASON_MISSING = "g0_missing_fields"
G0_REASON_HASH_MISMATCH = "g0_hash_mismatch"
G0_REASON_NOT_FROZEN = "g0_not_frozen"
G0_REASON_BINDING_MISMATCH = "g0_binding_mismatch"
G0_REASON_INVALID = "g0_invalid_objective"

R0_SELECT = "SELECT"
R0_DEFER = "DEFER"
R0_REJECT = "REJECT"

_TAG_RANK = {"SELECT": 0, "DEFER": 1, "REJECT": 2}


def compose_tag_disposition(tags: list[dict] | None) -> tuple[str, str] | None:
    """本地 tag overlay。``None`` = 调用方未提供（旧测试/非 pipeline），跳过。

    提供了 ``tags``（可空列表）则：无 tag → DEFER；任一 REJECT → REJECT；
    未标注视为 DEFER；全 SELECT 才放行到 L1。
    """
    if tags is None:
        return None
    if not tags:
        return R0_DEFER, "r0_tags_missing"
    worst_rank = 0
    worst = R0_SELECT
    for tag in tags:
        if not isinstance(tag, dict):
            return R0_DEFER, "r0_tag_invalid"
        raw = tag.get("disposition")
        disposition = "DEFER" if raw in (None, "") else str(raw)
        rank = _TAG_RANK.get(disposition)
        if rank is None:
            return R0_DEFER, "r0_tag_unknown"
        if rank > worst_rank:
            worst_rank = rank
            worst = disposition
    if worst == R0_SELECT:
        return None
    if worst == R0_REJECT:
        return R0_REJECT, "r0_tag_reject"
    return R0_DEFER, "r0_tag_defer"

AUDIT_ALGORITHM_VERSION = "hmac-sha256-u64/v1"
_AUDIT_SCALE = Decimal("0.000000000001")


@dataclass(frozen=True)
class G0Result:
    ok: bool
    reason: str | None = None
    manifest_hash: str | None = None
    cohort_id: int | None = None
    cohort_key: str | None = None
    objective_contract_id: int | None = None
    strategy_version_id: int | None = None
    release_manifest_id: int | None = None
    objective_hash: str | None = None
    strategy_hash: str | None = None
    release_hash: str | None = None
    seed_hash: str | None = None
    policy_hashes: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class R0Result:
    result: str
    reason_code: str | None = None
    recheck_at: datetime | None = None
    recheck_condition: str | None = None
    audit_selected: bool = False
    audit_u: Decimal | None = None
    audit_probability: Decimal | None = None
    episode_id: int | None = None
    input_hash: str | None = None
    sampling_unit_hash: str | None = None


class ScreeningLogic:
    """All screening decisions bind to exact cohort control and append-only Gate facts."""

    def __init__(
        self,
        cohort: CohortRepository,
        workflow: WorkflowRepository | None = None,
    ) -> None:
        self._cohort = cohort
        self._workflow = workflow or WorkflowRepository()

    async def run_g0(
        self,
        uow: UnitOfWork,
        *,
        cohort_id: int,
        objective_content: dict | None = None,
        expected_objective_hash: str | None = None,
    ) -> G0Result:
        """Validate DB-bound active objective/strategy/release and every exact policy freeze."""

        context = await self._cohort.get_g0_context(uow.session, cohort_id)
        if context is None or context["status"] != "OPEN":
            return G0Result(False, G0_REASON_NOT_FROZEN, cohort_id=cohort_id)
        if (
            context["objective_status"] != "active"
            or context["strategy_status"] != "active"
            or context["release_status"] != "active"
            or context["release_strategy_version_id"] != context["strategy_version_id"]
        ):
            return G0Result(False, G0_REASON_BINDING_MISMATCH, cohort_id=cohort_id)

        authoritative_content = context["objective_content"]
        try:
            objective = G0ObjectiveInput(content=authoritative_content)
        except ValidationError:
            return G0Result(False, G0_REASON_INVALID, cohort_id=cohort_id)
        if objective.missing_fields:
            return G0Result(False, G0_REASON_MISSING, cohort_id=cohort_id)
        actual_objective_hash = canonical_hash(authoritative_content)
        if actual_objective_hash != context["objective_hash"]:
            return G0Result(False, G0_REASON_HASH_MISMATCH, cohort_id=cohort_id)
        if objective_content is not None and canonical_hash(objective_content) != actual_objective_hash:
            return G0Result(False, G0_REASON_HASH_MISMATCH, cohort_id=cohort_id)
        if expected_objective_hash is not None and expected_objective_hash != actual_objective_hash:
            return G0Result(False, G0_REASON_HASH_MISMATCH, cohort_id=cohort_id)

        policy_hashes = context["policy_hashes"]
        if not isinstance(policy_hashes, dict):
            return G0Result(False, G0_REASON_NOT_FROZEN, cohort_id=cohort_id)
        required = set(REQUIRED_COHORT_POLICIES)
        if not required.issubset(policy_hashes):
            return G0Result(False, G0_REASON_NOT_FROZEN, cohort_id=cohort_id)
        if any(not _is_sha256(policy_hashes[name]) for name in required):
            return G0Result(False, G0_REASON_NOT_FROZEN, cohort_id=cohort_id)
        freezes = await self._cohort.frozen_policies(
            uow.session,
            cohort_key=context["cohort_key"],
            release_manifest_id=context["release_manifest_id"],
            policy_hashes={name: policy_hashes[name] for name in required},
        )
        if set(freezes) != required:
            return G0Result(False, G0_REASON_NOT_FROZEN, cohort_id=cohort_id)

        manifest = {
            "cohort_key": context["cohort_key"],
            "objective_key": context["objective_key"],
            "objective_version": context["objective_version"],
            "objective_hash": actual_objective_hash,
            "strategy_key": context["strategy_key"],
            "strategy_version": context["strategy_version"],
            "strategy_hash": context["strategy_hash"],
            "release_name": context["release_name"],
            "release_hash": context["release_hash"],
            "seed_hash": context["seed_hash"],
            "policies": {name: policy_hashes[name] for name in sorted(required)},
        }
        return G0Result(
            True,
            manifest_hash=canonical_hash(manifest),
            cohort_id=cohort_id,
            cohort_key=context["cohort_key"],
            objective_contract_id=context["objective_contract_id"],
            strategy_version_id=context["strategy_version_id"],
            release_manifest_id=context["release_manifest_id"],
            objective_hash=actual_objective_hash,
            strategy_hash=context["strategy_hash"],
            release_hash=context["release_hash"],
            seed_hash=context["seed_hash"],
            policy_hashes={name: policy_hashes[name] for name in sorted(required)},
        )

    async def enroll_frame(
        self,
        uow: UnitOfWork,
        *,
        cohort_id: int,
        frame: HydratedUniverseFrameInput,
        observed_at: datetime,
        ingested_at: datetime,
        g0: G0Result,
    ) -> int:
        """Enroll only an externally hydrated, exact COMPLETE frame; never query current projection."""

        await self._verify_g0(uow, g0, cohort_id)
        stored = await self._cohort.get_complete_frame(uow.session, frame.frame_id)
        if stored is None:
            raise ValueError("hydrated_frame_not_complete")
        if (
            stored["content_hash"] != frame.content_hash
            or stored["artifact_id"] != frame.artifact_object_id
            or stored["artifact_ref"] != frame.artifact_ref
            or stored["total_markets"] != len(frame.markets)
        ):
            raise ValueError("hydrated_frame_manifest_mismatch")

        return await self._cohort.upsert_confirmed_memberships(
            uow.session,
            cohort_id=cohort_id,
            frame_id=frame.frame_id,
            first_observed_at=observed_at,
            first_ingested_at=ingested_at,
            rows=[
                {
                    "market_id": market.market_id,
                    "metadata_hash": canonical_hash(market.metadata),
                }
                for market in frame.markets
            ],
        )

    async def enroll_hint(
        self,
        uow: UnitOfWork,
        *,
        cohort_id: int,
        market_id: int,
        metadata: dict,
        observed_at: datetime,
        ingested_at: datetime,
        g0: G0Result,
    ) -> bool:
        await self._verify_g0(uow, g0, cohort_id)
        return await self._cohort.upsert_membership(
            uow.session,
            cohort_id=cohort_id,
            market_id=market_id,
            first_seen_source="WS_HINT",
            first_observed_at=observed_at,
            first_ingested_at=ingested_at,
            metadata_hash=canonical_hash(metadata),
        )

    async def run_r0(
        self,
        uow: UnitOfWork,
        *,
        cohort_id: int,
        market_id: int,
        episode_no: int,
        r0_input: R0Input,
        g0: G0Result,
        r0_policy: R0PolicyInput,
        audit_policy: RejectAuditPolicyInput,
    ) -> R0Result:
        await self._verify_g0(uow, g0, cohort_id)
        if not r0_input.allowlist_ok():
            raise RuntimeError("r0_allowlist_contract_drift")
        membership = await self._cohort.get_confirmed_membership(
            uow.session, cohort_id=cohort_id, market_id=market_id
        )
        if membership is None:
            raise ValueError("r0_confirmed_membership_required")

        r0_policy_hash = canonical_hash(r0_policy.model_dump(mode="json"))
        audit_policy_hash = canonical_hash(audit_policy.model_dump(mode="json"))
        if (
            g0.policy_hashes.get("r0") != r0_policy_hash
            or g0.policy_hashes.get("reject_audit") != audit_policy_hash
        ):
            raise ValueError("r0_policy_freeze_mismatch")
        if audit_policy.algorithm_version != AUDIT_ALGORITHM_VERSION:
            raise ValueError("r0_audit_algorithm_mismatch")
        if r0_input.objective_ref not in (None, g0.objective_hash):
            raise ValueError("r0_objective_ref_mismatch")

        r0_dict = r0_input.model_dump(exclude_none=True, mode="json")
        input_hash = canonical_hash(r0_dict)
        result, reason = self._decide(r0_input, r0_policy)
        if result == R0_DEFER:
            recheck_at = r0_input.end_at
            recheck_condition = r0_policy.defer_recheck_condition
        elif result == R0_REJECT:
            recheck_at = None
            recheck_condition = r0_policy.reject_recheck_condition
        else:
            recheck_at = None
            recheck_condition = None

        sampling_unit_hash = canonical_hash(
            {
                "cohort_key": g0.cohort_key,
                "market_key": membership["market_key"],
                "episode_no": episode_no,
                "frame_content_hash": membership["frame_content_hash"],
                "input_hash": input_hash,
                "r0_policy_hash": r0_policy_hash,
            }
        )
        audit_selected = False
        audit_u: Decimal | None = None
        audit_probability: Decimal | None = None
        if result != R0_SELECT:
            stratum = f"r0:{result}:{reason}"
            rate = (
                audit_policy.reject_probability
                if result == R0_REJECT
                else audit_policy.defer_probability
            )
            selected, exact_u, probability = deterministic_sample(
                content_hash=sampling_unit_hash,
                seed_hash=g0.seed_hash or "",
                stratum=stratum,
                rate=rate,
                salt=audit_policy.salt,
            )
            audit_selected = selected
            audit_u = exact_u.quantize(_AUDIT_SCALE, rounding=ROUND_DOWN)
            audit_probability = probability.quantize(_AUDIT_SCALE)
            await self._cohort.insert_audit_sample(
                uow.session,
                cohort_id=cohort_id,
                target="r0",
                content_hash=sampling_unit_hash,
                stratum=stratum,
                seed_hash=g0.seed_hash,
                algorithm_hash=canonical_hash(
                    {
                        "algorithm": AUDIT_ALGORITHM_VERSION,
                        "salt": audit_policy.salt,
                        "policy_hash": audit_policy_hash,
                    }
                ),
                u=audit_u,
                inclusion_probability=audit_probability,
                selected=selected,
            )

        episode_id = await self._cohort.insert_screening_episode(
            uow.session,
            cohort_id=cohort_id,
            market_id=market_id,
            episode_no=episode_no,
            objective_contract_id=g0.objective_contract_id,
            input_snapshot=r0_dict,
            # DB screening identity is the full market/frame/policy-bound sampling
            # unit, not the DTO-only hash (identical DTOs across markets must not
            # collapse into one audit unit).
            input_hash=sampling_unit_hash,
            result=result,
            reason_code=reason,
            recheck_at=recheck_at,
            recheck_condition=recheck_condition,
            audit_assigned=audit_selected,
        )
        committed_at = datetime.now(timezone.utc)
        await self._workflow.insert_gate_decision(
            uow.session,
            gate="G0",
            target_kind="screening",
            target_id=episode_id,
            input_hash=g0.manifest_hash,
            policy_hash=canonical_hash(g0.policy_hashes),
            version_manifest_id=g0.release_manifest_id,
            result="PASS",
            reason_code=None,
            committed_at=committed_at,
        )
        await self._workflow.insert_gate_decision(
            uow.session,
            gate="R0",
            target_kind="screening",
            target_id=episode_id,
            input_hash=sampling_unit_hash,
            policy_hash=r0_policy_hash,
            version_manifest_id=g0.release_manifest_id,
            result=result,
            reason_code=reason,
            committed_at=committed_at,
        )
        return R0Result(
            result=result,
            reason_code=reason,
            recheck_at=recheck_at,
            recheck_condition=recheck_condition,
            audit_selected=audit_selected,
            audit_u=audit_u,
            audit_probability=audit_probability,
            episode_id=episode_id,
            input_hash=input_hash,
            sampling_unit_hash=sampling_unit_hash,
        )

    async def run_r0_batch(
        self,
        uow: UnitOfWork,
        *,
        cohort_id: int,
        items: list[R0BatchItemInput],
        g0: G0Result,
        r0_policy: R0PolicyInput,
        audit_policy: RejectAuditPolicyInput,
    ) -> list[R0Result]:
        """Evaluate and persist a hydrated frame batch with real Gate constraints.

        The decision and sampling code is identical to ``run_r0``; only persistence
        is set-based so the explicit 50k/60s contract is attainable without
        unbounded connections or bypassing Logic.
        """

        await self._verify_g0(uow, g0, cohort_id)
        keys = [(item.market_id, item.episode_no) for item in items]
        if len(keys) != len(set(keys)):
            raise ValueError("r0_batch_duplicate_key")
        if any(not item.r0_input.allowlist_ok() for item in items):
            raise RuntimeError("r0_allowlist_contract_drift")

        r0_policy_hash = canonical_hash(r0_policy.model_dump(mode="json"))
        audit_policy_hash = canonical_hash(audit_policy.model_dump(mode="json"))
        if (
            g0.policy_hashes.get("r0") != r0_policy_hash
            or g0.policy_hashes.get("reject_audit") != audit_policy_hash
        ):
            raise ValueError("r0_policy_freeze_mismatch")
        if audit_policy.algorithm_version != AUDIT_ALGORITHM_VERSION:
            raise ValueError("r0_audit_algorithm_mismatch")

        memberships = await self._cohort.get_confirmed_memberships(
            uow.session,
            cohort_id=cohort_id,
            market_ids=[item.market_id for item in items],
        )
        if set(memberships) != {item.market_id for item in items}:
            raise ValueError("r0_confirmed_membership_required")

        prepared: list[dict] = []
        audit_rows: list[dict] = []
        algorithm_hash = canonical_hash(
            {
                "algorithm": AUDIT_ALGORITHM_VERSION,
                "salt": audit_policy.salt,
                "policy_hash": audit_policy_hash,
            }
        )
        for item in items:
            r0_input = item.r0_input
            if r0_input.objective_ref not in (None, g0.objective_hash):
                raise ValueError("r0_objective_ref_mismatch")
            snapshot = r0_input.model_dump(exclude_none=True, mode="json")
            dto_hash = canonical_hash(snapshot)
            result, reason = self._decide(r0_input, r0_policy)
            if result == R0_DEFER:
                recheck_at = r0_input.end_at
                recheck_condition = r0_policy.defer_recheck_condition
            elif result == R0_REJECT:
                recheck_at = None
                recheck_condition = r0_policy.reject_recheck_condition
            else:
                recheck_at = None
                recheck_condition = None
            membership = memberships[item.market_id]
            sampling_hash = canonical_hash(
                {
                    "cohort_key": g0.cohort_key,
                    "market_key": membership["market_key"],
                    "episode_no": item.episode_no,
                    "frame_content_hash": membership["frame_content_hash"],
                    "input_hash": dto_hash,
                    "r0_policy_hash": r0_policy_hash,
                }
            )
            selected = False
            u_value = None
            probability = None
            if result != R0_SELECT:
                stratum = f"r0:{result}:{reason}"
                rate = (
                    audit_policy.reject_probability
                    if result == R0_REJECT
                    else audit_policy.defer_probability
                )
                selected, exact_u, exact_probability = deterministic_sample(
                    content_hash=sampling_hash,
                    seed_hash=g0.seed_hash or "",
                    stratum=stratum,
                    rate=rate,
                    salt=audit_policy.salt,
                )
                u_value = exact_u.quantize(_AUDIT_SCALE, rounding=ROUND_DOWN)
                probability = exact_probability.quantize(_AUDIT_SCALE)
                audit_rows.append(
                    {
                        "target": "r0",
                        "content_hash": sampling_hash,
                        "stratum": stratum,
                        "seed_hash": g0.seed_hash,
                        "algorithm_hash": algorithm_hash,
                        "u": str(u_value),
                        "inclusion_probability": str(probability),
                        "selected": selected,
                    }
                )
            prepared.append(
                {
                    "market_id": item.market_id,
                    "episode_no": item.episode_no,
                    "objective_contract_id": g0.objective_contract_id,
                    "input_snapshot": snapshot,
                    "input_hash": sampling_hash,
                    "result": result,
                    "reason_code": reason,
                    "recheck_at": recheck_at.isoformat() if recheck_at else None,
                    "recheck_condition": recheck_condition,
                    "audit_assigned": selected,
                    "dto_hash": dto_hash,
                    "audit_u": u_value,
                    "audit_probability": probability,
                }
            )

        await self._cohort.insert_audit_samples_bulk(
            uow.session, cohort_id=cohort_id, rows=audit_rows
        )
        episodes = await self._cohort.insert_screening_episodes_bulk(
            uow.session, cohort_id=cohort_id, rows=prepared
        )
        committed_at = datetime.now(timezone.utc).isoformat()
        g0_rows: list[dict] = []
        r0_rows: list[dict] = []
        output: list[R0Result] = []
        for row in prepared:
            episode = episodes[(row["market_id"], row["episode_no"])]
            gate_common = {
                "target_id": episode["id"],
                "version_manifest_id": g0.release_manifest_id,
                "committed_at": committed_at,
            }
            g0_rows.append(
                {
                    **gate_common,
                    "input_hash": g0.manifest_hash,
                    "policy_hash": canonical_hash(g0.policy_hashes),
                    "result": "PASS",
                    "reason_code": None,
                }
            )
            r0_rows.append(
                {
                    **gate_common,
                    "input_hash": row["input_hash"],
                    "policy_hash": r0_policy_hash,
                    "result": row["result"],
                    "reason_code": row["reason_code"],
                }
            )
            output.append(
                R0Result(
                    result=row["result"],
                    reason_code=row["reason_code"],
                    recheck_at=episode["recheck_at"],
                    recheck_condition=row["recheck_condition"],
                    audit_selected=row["audit_assigned"],
                    audit_u=row["audit_u"],
                    audit_probability=row["audit_probability"],
                    episode_id=episode["id"],
                    input_hash=row["dto_hash"],
                    sampling_unit_hash=row["input_hash"],
                )
            )
        await self._workflow.insert_gate_decisions_bulk(
            uow.session,
            gate="G0",
            target_kind="screening",
            rows=g0_rows,
        )
        await self._workflow.insert_gate_decisions_bulk(
            uow.session,
            gate="R0",
            target_kind="screening",
            rows=r0_rows,
        )
        return output

    def _decide(
        self, r0_input: R0Input, policy: R0PolicyInput
    ) -> tuple[str, str | None]:
        tags = (
            r0_input.market_metadata.get("tags")
            if "tags" in r0_input.market_metadata
            else None
        )
        tag_gate = compose_tag_disposition(tags)
        if tag_gate is not None:
            return tag_gate
        if policy.require_two_sided_quote and (
            r0_input.best_bid is None or r0_input.best_ask is None
        ):
            return R0_DEFER, "r0_missing_quote"
        if (
            r0_input.best_bid is not None
            and r0_input.best_ask is not None
            and r0_input.best_bid >= r0_input.best_ask
        ):
            return R0_DEFER, "r0_crossed_book"
        if (
            r0_input.rule_completeness is not None
            and r0_input.rule_completeness < policy.minimum_rule_completeness
        ):
            return R0_DEFER, "r0_rules_incomplete"
        if (
            r0_input.minimum_deployable_capacity is not None
            and r0_input.minimum_deployable_capacity <= 0
        ):
            return R0_REJECT, "r0_zero_capacity"
        if (
            r0_input.estimated_research_cost is not None
            and r0_input.estimated_research_cost > policy.maximum_research_cost
        ):
            return R0_REJECT, "r0_cost_too_high"
        return R0_SELECT, None

    @staticmethod
    def _require_g0(g0: G0Result, cohort_id: int) -> None:
        if not g0.ok or g0.cohort_id != cohort_id or not g0.manifest_hash:
            raise ValueError("g0_pass_evidence_required")

    async def _verify_g0(
        self, uow: UnitOfWork, supplied: G0Result, cohort_id: int
    ) -> None:
        self._require_g0(supplied, cohort_id)
        current = await self.run_g0(uow, cohort_id=cohort_id)
        if not current.ok or current.manifest_hash != supplied.manifest_hash:
            raise ValueError("g0_evidence_stale_or_forged")


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )
