"""DB-evidenced G0→R0→G1→G2→R1→G4→G5A→G5B→G6→G7A→G7B state machine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.db.uow import UnitOfWork
from app.domain.trading.hashing import canonical_hash
from app.domain.trading.gates import assert_frozen_gate_binding
from app.repositories.trading.workflow import WorkflowRepository

ORDER = ("G0", "R0", "G1", "G2", "R1", "G4", "G5A", "G5B", "G6", "G7A", "G7B")
_INDEX = {gate: index for index, gate in enumerate(ORDER)}


class IllegalTransitionError(RuntimeError):
    pass


@dataclass(frozen=True)
class EpisodeInput:
    decision_opportunity_id: int
    component_version_id: int
    strategy_version_id: int
    objective_contract_id: int
    trigger: str
    cutoff_at: datetime
    horizon: str
    experiment_variant: str
    contract_spec_ids: list[int]


@dataclass(frozen=True)
class EpisodeKeyMaterial:
    opportunity_key: str
    component_version_hash: str
    strategy_hash: str
    objective_hash: str
    trigger: str
    cutoff_at: datetime
    horizon: str
    experiment_variant: str
    spec_hashes: list[str]


def episode_key(material: EpisodeKeyMaterial) -> str:
    return canonical_hash(
        {
            "opportunity_key": material.opportunity_key,
            "component_version_hash": material.component_version_hash,
            "strategy_hash": material.strategy_hash,
            "objective_hash": material.objective_hash,
            "spec_hashes": sorted(material.spec_hashes),
            "trigger": material.trigger,
            "cutoff": material.cutoff_at,
            "horizon": material.horizon,
            "variant": material.experiment_variant,
        }
    )


class TradingStateMachine:
    def __init__(self, workflow: WorkflowRepository) -> None:
        self._wf = workflow

    def assert_order(self, from_gate: str, to_gate: str) -> None:
        if from_gate not in _INDEX or to_gate not in _INDEX:
            raise IllegalTransitionError(f"unknown_gate:{from_gate}->{to_gate}")
        if _INDEX[to_gate] != _INDEX[from_gate] + 1:
            raise IllegalTransitionError(f"illegal_transition:{from_gate}->{to_gate}")

    async def create_parent_opportunity(
        self,
        uow: UnitOfWork,
        *,
        cohort_id: int,
        chain_type: str,
        objective_contract_id: int,
        strategy_version_id: int,
        source_screening_episode_id: int | None,
        triggered_at: datetime,
        market_ids: list[int],
        audit_tag: str | None = None,
    ) -> int:
        self.assert_order("G0", "R0")
        if source_screening_episode_id is None:
            raise IllegalTransitionError("parent_requires_screening")
        screening = await self._wf.get_screening_chain(
            uow.session, source_screening_episode_id
        )
        if screening is None:
            raise IllegalTransitionError("screening_missing")
        if (
            screening["cohort_id"] != cohort_id
            or screening["objective_contract_id"] != objective_contract_id
            or screening["cohort_objective_contract_id"] != objective_contract_id
            or screening["cohort_strategy_version_id"] != strategy_version_id
            or set(market_ids) != {screening["market_id"]}
        ):
            raise IllegalTransitionError("parent_screening_binding_mismatch")
        g0 = await self._wf.get_gate_decision(
            uow.session, gate="G0", target_kind="screening", target_id=source_screening_episode_id
        )
        r0 = await self._wf.get_gate_decision(
            uow.session, gate="R0", target_kind="screening", target_id=source_screening_episode_id
        )
        policies = screening.get("policy_hashes")
        if (
            g0 is None
            or g0["result"] != "PASS"
            or r0 is None
            or r0["result"] != screening["result"]
            or g0["version_manifest_id"] != screening["release_manifest_id"]
            or r0["version_manifest_id"] != screening["release_manifest_id"]
            or not isinstance(policies, dict)
            or g0["policy_hash"] != canonical_hash(policies)
            or r0["policy_hash"] != policies.get("r0")
        ):
            raise IllegalTransitionError("g0_r0_evidence_missing")
        audited = screening["result"] != "SELECT" and screening["audit_assigned"]
        if screening["result"] != "SELECT" and not audited:
            raise IllegalTransitionError("r0_not_opportunity_eligible")
        if audited:
            if chain_type != "RESEARCH_EVAL" or audit_tag != "R0_REJECT_AUDIT":
                raise IllegalTransitionError("r0_audit_lineage_required")
        elif chain_type != "DECISION" or audit_tag is not None:
            raise IllegalTransitionError("selected_parent_must_be_decision")
        key = canonical_hash(
            {
                "kind": "parent",
                "cohort_key": screening["cohort_key"],
                "r0_input_hash": r0["input_hash"],
                "triggered_at": triggered_at,
            }
        )
        opportunity_id = await self._wf.insert_opportunity(
            uow.session,
            opportunity_key=key,
            cohort_id=cohort_id,
            parent_id=None,
            chain_type=chain_type,
            objective_contract_id=objective_contract_id,
            strategy_version_id=strategy_version_id,
            source_screening_episode_id=source_screening_episode_id,
            triggered_at=triggered_at,
            audit_tag=audit_tag,
            g0_manifest_hash=g0["input_hash"],
        )
        for market_id in sorted(set(market_ids)):
            await self._wf.insert_opportunity_market(
                uow.session, opportunity_id=opportunity_id, market_id=market_id
            )
        return opportunity_id

    async def create_g1_child(
        self,
        uow: UnitOfWork,
        *,
        parent_id: int,
        cohort_id: int,
        chain_type: str,
        objective_contract_id: int,
        strategy_version_id: int,
        triggered_at: datetime,
        market_id: int,
        seq: int = 0,
    ) -> int:
        self.assert_order("R0", "G1")
        parent = await self._require_parent(uow, parent_id)
        self._assert_child_binding(parent, cohort_id, chain_type, objective_contract_id, strategy_version_id)
        if market_id not in await self._wf.opportunity_market_ids(uow.session, parent_id):
            raise IllegalTransitionError("g1_market_not_in_parent")
        market_key = await self._wf.market_key(uow.session, market_id)
        child = await self._wf.insert_opportunity(
            uow.session,
            opportunity_key=canonical_hash(
                {"kind": "g1", "parent": parent["opportunity_key"], "market": market_key, "seq": seq}
            ),
            cohort_id=cohort_id,
            parent_id=parent_id,
            chain_type=chain_type,
            objective_contract_id=objective_contract_id,
            strategy_version_id=strategy_version_id,
            source_screening_episode_id=None,
            triggered_at=triggered_at,
            audit_tag=parent["audit_tag"],
            g0_manifest_hash=parent["g0_manifest_hash"],
        )
        await self._wf.insert_opportunity_market(uow.session, opportunity_id=child, market_id=market_id)
        return child

    async def create_g2_child(
        self,
        uow: UnitOfWork,
        *,
        parent_id: int,
        cohort_id: int,
        chain_type: str,
        objective_contract_id: int,
        strategy_version_id: int,
        triggered_at: datetime,
        component_key: str,
        g1_child_ids: list[int] | None = None,
    ) -> int:
        self.assert_order("G1", "G2")
        parent = await self._require_parent(uow, parent_id)
        self._assert_child_binding(parent, cohort_id, chain_type, objective_contract_id, strategy_version_id)
        if not g1_child_ids or len(g1_child_ids) != len(set(g1_child_ids)):
            raise IllegalTransitionError("g2_requires_g1_children")
        stable_children: list[str] = []
        market_ids: set[int] = set()
        for child_id in sorted(set(g1_child_ids)):
            child = await self._wf.get_opportunity(uow.session, child_id)
            gate = await self._wf.get_gate_decision(
                uow.session, gate="G1", target_kind="opportunity", target_id=child_id
            )
            if (
                child is None
                or child["parent_id"] != parent_id
                or child["status"] != "OPEN"
                or child["cohort_id"] != cohort_id
                or child["chain_type"] != chain_type
                or child["objective_contract_id"] != objective_contract_id
                or child["strategy_version_id"] != strategy_version_id
                or gate is None
                or gate["result"] != "PASS"
            ):
                raise IllegalTransitionError("g1_pass_evidence_missing")
            stable_children.append(child["opportunity_key"])
            market_ids.update(
                await self._wf.opportunity_market_ids(uow.session, child_id)
            )
        if not market_ids:
            raise IllegalTransitionError("g2_requires_g1_markets")
        child_id = await self._wf.insert_opportunity(
            uow.session,
            opportunity_key=canonical_hash(
                {"kind": "g2", "parent": parent["opportunity_key"], "component": component_key,
                 "g1_children": sorted(stable_children)}
            ),
            cohort_id=cohort_id,
            parent_id=parent_id,
            chain_type=chain_type,
            objective_contract_id=objective_contract_id,
            strategy_version_id=strategy_version_id,
            source_screening_episode_id=None,
            triggered_at=triggered_at,
            audit_tag=parent["audit_tag"],
            g0_manifest_hash=parent["g0_manifest_hash"],
        )
        for market_id in sorted(market_ids):
            await self._wf.insert_opportunity_market(
                uow.session, opportunity_id=child_id, market_id=market_id
            )
        return child_id

    async def terminal_g1_fail(self, uow: UnitOfWork, child_opp_id: int, reason: str) -> bool:
        gate = await self._wf.get_gate_decision(
            uow.session, gate="G1", target_kind="opportunity", target_id=child_opp_id
        )
        if gate is None or gate["result"] != "FAIL" or gate["reason_code"] != reason:
            raise IllegalTransitionError("g1_fail_evidence_missing")
        return await self._wf.terminal_opportunity(
            uow.session, child_opp_id, terminal_reason=reason, disposition="rejected"
        )

    async def terminal_g2_fail(self, uow: UnitOfWork, child_opp_id: int, reason: str) -> bool:
        gate = await self._wf.get_gate_decision(
            uow.session, gate="G2", target_kind="opportunity", target_id=child_opp_id
        )
        if gate is None or gate["result"] != "FAIL" or gate["reason_code"] != reason:
            raise IllegalTransitionError("g2_fail_evidence_missing")
        return await self._wf.terminal_opportunity(
            uow.session, child_opp_id, terminal_reason=reason, disposition="failed"
        )

    async def create_episode(self, uow: UnitOfWork, *, input_: EpisodeInput) -> int:
        gate = await self._wf.get_gate_decision(
            uow.session, gate="G2", target_kind="opportunity",
            target_id=input_.decision_opportunity_id
        )
        if gate is None or gate["result"] != "PASS":
            raise IllegalTransitionError("g2_pass_evidence_missing")
        binding = await self._wf.episode_binding(
            uow.session,
            opportunity_id=input_.decision_opportunity_id,
            component_version_id=input_.component_version_id,
        )
        if binding is None or binding["opportunity_status"] not in {"OPEN", "ROUTED"}:
            raise IllegalTransitionError("episode_binding_missing")
        if (
            binding["component_version_status"] != "active"
            or binding["schema_status"] != "active"
            or binding["strategy_version_id"] != input_.strategy_version_id
            or binding["objective_contract_id"] != input_.objective_contract_id
            or len(input_.contract_spec_ids) != len(set(input_.contract_spec_ids))
            or sorted(input_.contract_spec_ids) != sorted(binding["contract_spec_ids"])
        ):
            raise IllegalTransitionError("episode_binding_mismatch")
        key = episode_key(EpisodeKeyMaterial(
            opportunity_key=binding["opportunity_key"],
            component_version_hash=binding["component_version_hash"],
            strategy_hash=binding["strategy_hash"],
            objective_hash=binding["objective_hash"],
            trigger=input_.trigger,
            cutoff_at=input_.cutoff_at,
            horizon=input_.horizon,
            experiment_variant=input_.experiment_variant,
            spec_hashes=binding["spec_hashes"],
        ))
        episode_id = await self._wf.insert_episode(
            uow.session, episode_key=key,
            decision_opportunity_id=input_.decision_opportunity_id,
            component_version_id=input_.component_version_id,
            strategy_version_id=input_.strategy_version_id,
            objective_contract_id=input_.objective_contract_id,
            trigger=input_.trigger, cutoff_at=input_.cutoff_at, horizon=input_.horizon,
            experiment_variant=input_.experiment_variant,
        )
        for spec_id in binding["contract_spec_ids"]:
            await self._wf.insert_episode_spec(
                uow.session, episode_id=episode_id, contract_spec_id=spec_id
            )
        if not await self._wf.route_opportunity(
            uow.session, input_.decision_opportunity_id
        ):
            raise IllegalTransitionError("opportunity_route_conflict")
        return episode_id

    async def route_episode(
        self,
        uow: UnitOfWork,
        *,
        episode_id: int,
        route_channel: str,
        first_rejected_gate: str | None,
        reason_code: str | None,
        recheck_at: datetime | None,
        recheck_condition: str | None,
        audit_selected: bool,
        policy_hash: str | None = None,
        version_manifest_id: int | None = None,
    ) -> None:
        self.assert_order("G2", "R1")
        if policy_hash is None or version_manifest_id is None:
            raise ValueError("r1_policy_binding_required")
        if route_channel not in {"reject", "shallow", "standard", "deep"}:
            raise ValueError("r1_route_unknown")
        episode = await self._wf.get_episode(uow.session, episode_id)
        if episode is None:
            raise IllegalTransitionError("episode_missing")
        g2 = await self._wf.get_gate_decision(
            uow.session, gate="G2", target_kind="opportunity",
            target_id=episode["decision_opportunity_id"]
        )
        if g2 is None or g2["result"] != "PASS":
            raise IllegalTransitionError("g2_pass_evidence_missing")
        lineage = await self._wf.get_opportunity_lineage(
            uow.session, episode["decision_opportunity_id"]
        )
        if lineage is None:
            raise IllegalTransitionError("episode_opportunity_missing")
        assert_frozen_gate_binding(
            lineage,
            policy_type="r1",
            policy_hash=policy_hash,
            version_manifest_id=version_manifest_id,
        )
        if route_channel == "reject" and (not reason_code or not (recheck_at or recheck_condition)):
            raise ValueError("r1_reject_reason_recheck_required")
        if route_channel == "reject" and first_rejected_gate not in ORDER:
            raise ValueError("r1_first_rejected_gate_required")
        if route_channel != "reject" and any(
            value is not None
            for value in (first_rejected_gate, reason_code, recheck_at, recheck_condition)
        ):
            raise ValueError("r1_non_reject_must_not_carry_rejection")
        if audit_selected and route_channel != "reject":
            raise ValueError("r1_audit_only_for_reject")
        input_hash = canonical_hash({
            "episode_key": episode["episode_key"], "route": route_channel,
            "first_rejected_gate": first_rejected_gate, "reason": reason_code,
            "recheck_at": recheck_at, "recheck_condition": recheck_condition,
            "audit_selected": audit_selected,
        })
        await self._wf.insert_gate_decision(
            uow.session, gate="R1", target_kind="episode", target_id=episode_id,
            input_hash=input_hash, policy_hash=policy_hash,
            version_manifest_id=version_manifest_id, result=route_channel,
            reason_code=reason_code, committed_at=datetime.now(timezone.utc),
        )
        # WP-01C has not passed G4..G7; no route has action/qualification/capital authority yet.
        await self._wf.insert_episode_membership(
            uow.session, episode_id=episode_id, route_channel=route_channel,
            first_rejected_gate=first_rejected_gate, reason_code=reason_code,
            recheck_at=recheck_at, recheck_condition=recheck_condition,
            processing_disposition="rejected" if route_channel == "reject" else "completed",
            action_eligible=False, qualification_eligible=False,
            capital_evidence_eligible=False, audit_selected=audit_selected,
        )
        if route_channel == "reject":
            await self._wf.terminal_episode(
                uow.session, episode_id, drop_reason=reason_code or "rejected"
            )
        else:
            await self._wf.mark_episode_routed(uow.session, episode_id)

    async def terminal_g6_fail(
        self, uow: UnitOfWork, episode_id: int, reason: str
    ) -> bool:
        """G6 hard check 失败 → episode 进入 PRE_COMMIT_TERMINAL（不生成 committed submission）。"""
        gate = await self._wf.get_gate_decision(
            uow.session, gate="G6", target_kind="episode", target_id=episode_id
        )
        if gate is None or gate["result"] not in ("FAIL",):
            raise IllegalTransitionError("g6_fail_evidence_missing")
        if reason and reason != gate["reason_code"]:
            raise IllegalTransitionError("g6_fail_reason_mismatch")
        episode = await self._wf.get_episode(uow.session, episode_id)
        if episode is None or episode["status"] != "ROUTED":
            raise IllegalTransitionError("episode_not_routed")
        result = await self._wf.terminal_episode(
            uow.session, episode_id, drop_reason=reason
        )
        if result:
            return True
        existing = await self._wf.get_episode(uow.session, episode_id)
        return bool(
            existing
            and existing["status"] == "PRE_COMMIT_TERMINAL"
            and existing["drop_reason"] == reason
        )

    async def _require_parent(self, uow: UnitOfWork, parent_id: int) -> dict:
        parent = await self._wf.get_opportunity(uow.session, parent_id)
        if parent is None or parent["parent_id"] is not None or parent["status"] != "OPEN":
            raise IllegalTransitionError("parent_not_open")
        return parent

    @staticmethod
    def _assert_child_binding(parent: dict, cohort_id: int, chain_type: str,
                              objective_contract_id: int, strategy_version_id: int) -> None:
        if (
            parent["cohort_id"] != cohort_id or parent["chain_type"] != chain_type
            or parent["objective_contract_id"] != objective_contract_id
            or parent["strategy_version_id"] != strategy_version_id
        ):
            raise IllegalTransitionError("child_parent_binding_mismatch")
