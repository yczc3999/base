"""G2 finite-world component compiler and persistence orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from itertools import product
from typing import Any

from app.db.uow import UnitOfWork
from app.domain.trading.hashing import canonical_bytes, canonical_hash
from app.domain.trading.gates import assert_frozen_gate_binding
from app.domain.trading.payout import FORBIDDEN_STATES
from app.repositories.trading.semantics import SemanticsRepository
from app.repositories.trading.workflow import WorkflowRepository
from app.schemas.trading.semantics import (
    KNOWN_RESOLUTION_STATES,
    SCHEMA_FORBIDDEN_KEYS,
    WorldSchemaInput,
)

KNOWN_STATES = KNOWN_RESOLUTION_STATES

G2_OK = None
G2_SCHEMA_FORBIDDEN = "g2_schema_forbidden_field"
G2_STATE_BUDGET = "g2_state_budget_exceeded"
G2_HC_NOT_TOTAL = "g2_hc_not_total"
G2_HC_BAD_STATE = "g2_hc_bad_state"
G2_CONSTRAINT_CONFLICT = "g2_constraint_conflict"
G2_COMPONENT_EMPTY = "g2_component_empty"
G2_SPEC_NOT_PASS = "g2_contract_spec_not_pass"

WORLD_STATE_BUDGET = 4096


def _forbidden_in(value: Any, path: str = "") -> str | None:
    """Recursively scan every schema branch, not only variables/domains."""

    forbidden = {field.casefold() for field in SCHEMA_FORBIDDEN_KEYS}
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if str(key).casefold() in forbidden:
                return child_path
            hit = _forbidden_in(child, child_path)
            if hit is not None:
                return hit
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            hit = _forbidden_in(child, f"{path}[{index}]")
            if hit is not None:
                return hit
    return None


def _predicate_matches(assignment: dict[str, Any], predicate: Any) -> bool:
    if not isinstance(predicate, dict) or set(predicate) != {"variable", "equals"}:
        raise ValueError("constraint_predicate_shape")
    variable = predicate["variable"]
    if variable not in assignment:
        raise ValueError("constraint_unknown_variable")
    return canonical_bytes(assignment[variable]) == canonical_bytes(predicate["equals"])


def _constraint_allows(assignment: dict[str, Any], constraint: Any) -> bool:
    """Small, closed, non-executable constraint IR evaluated over finite assignments."""

    if not isinstance(constraint, dict) or not isinstance(constraint.get("type"), str):
        raise ValueError("constraint_shape")
    kind = constraint["type"]
    if kind == "exclude":
        if set(constraint) != {"type", "when"}:
            raise ValueError("constraint_exclude_shape")
        return not _predicate_matches(assignment, constraint["when"])
    if kind == "requires":
        if set(constraint) != {"type", "if", "then"}:
            raise ValueError("constraint_requires_shape")
        return (
            not _predicate_matches(assignment, constraint["if"])
            or _predicate_matches(assignment, constraint["then"])
        )
    if kind in {"mutually_exclusive", "exactly_one"}:
        if set(constraint) != {"type", "predicates"}:
            raise ValueError(f"constraint_{kind}_shape")
        predicates = constraint["predicates"]
        if not isinstance(predicates, list) or not predicates:
            raise ValueError(f"constraint_{kind}_empty")
        matched = sum(_predicate_matches(assignment, item) for item in predicates)
        return matched <= 1 if kind == "mutually_exclusive" else matched == 1
    raise ValueError(f"constraint_type_unknown:{kind}")


def _finite_assignments(candidate: WorldSchemaInput) -> list[dict[str, Any]]:
    variables = sorted(candidate.domains)
    if not variables or set(candidate.variables) != set(candidate.domains):
        raise ValueError("variable_domain_mismatch")
    domains: list[list[Any]] = []
    state_space_size = 1
    for variable in variables:
        domain = candidate.domains[variable]
        if not isinstance(domain, list) or not domain:
            raise ValueError(f"domain_not_finite:{variable}")
        canonical_values = [canonical_bytes(value) for value in domain]
        if len(canonical_values) != len(set(canonical_values)):
            raise ValueError(f"domain_duplicate:{variable}")
        state_space_size *= len(domain)
        if state_space_size > WORLD_STATE_BUDGET:
            raise OverflowError("state_budget")
        domains.append(domain)

    valid: list[dict[str, Any]] = []
    for values in product(*domains):
        assignment = dict(zip(variables, values))
        if all(_constraint_allows(assignment, constraint) for constraint in candidate.constraints):
            valid.append(assignment)
    if not valid:
        raise ValueError("constraint_unsatisfiable")
    return valid


@dataclass(frozen=True)
class G2Result:
    ok: bool
    component_version_id: int | None = None
    reason: str | None = None
    content_hash: str | None = None

    @property
    def reason_code(self) -> str | None:
        return None if self.ok else self.reason


class ComponentLogic:
    """Validate exact finite worlds and exact per-spec R_c before publishing a version."""

    def __init__(
        self,
        semantics: SemanticsRepository,
        workflow: WorkflowRepository | None = None,
    ) -> None:
        self._sem = semantics
        self._workflow = workflow or WorkflowRepository()

    async def run_g2(
        self,
        uow: UnitOfWork,
        *,
        candidate: WorldSchemaInput,
        contract_spec_ids: list[int],
        member_hc: dict[int, dict[str, str]] | None = None,
        cost_budget: Decimal | int | None = None,
        opportunity_id: int | None = None,
        policy_hash: str | None = None,
        version_manifest_id: int | None = None,
    ) -> G2Result:
        session = uow.session
        if opportunity_id is None or policy_hash is None or version_manifest_id is None:
            raise ValueError("g2_gate_binding_required")
        lineage = await self._workflow.get_opportunity_lineage(session, opportunity_id)
        if lineage is None or lineage["parent_id"] is None or lineage["status"] != "OPEN":
            raise ValueError("g2_opportunity_not_open_child")
        assert_frozen_gate_binding(
            lineage,
            policy_type="taxonomy",
            policy_hash=policy_hash,
            version_manifest_id=version_manifest_id,
        )
        if isinstance(cost_budget, float):
            raise ValueError("g2_cost_budget_float_forbidden")

        normalized_ids = sorted(contract_spec_ids)
        specs = await self._sem.get_specs(session, normalized_ids)
        # Runtime G2 must compare the separately supplied member mapping with the
        # candidate mapping; omitting it would turn an exact-boundary check into a
        # self-consistency check.
        reason = (
            G2_HC_NOT_TOTAL
            if member_hc is None
            else self._validate(candidate, normalized_ids, member_hc, specs)
        )
        candidate_semantics = candidate.model_dump(mode="json")
        candidate_semantics.pop("h_c", None)
        stable_gate_members = [
            {
                "spec_hash": specs[spec_id]["content_hash"],
                "resolution_states": sorted(specs[spec_id]["kc_resolution_states"]),
                "h_c": candidate.h_c.get(str(spec_id)),
            }
            for spec_id in normalized_ids
            if spec_id in specs
        ]
        input_hash = canonical_hash(
            {
                "candidate": candidate_semantics,
                "members": stable_gate_members,
                "cost_budget": cost_budget,
            }
        )
        if reason is not None:
            await self._record_gate(
                session,
                opportunity_id=opportunity_id,
                input_hash=input_hash,
                policy_hash=policy_hash,
                version_manifest_id=version_manifest_id,
                ok=False,
                reason=reason,
            )
            return G2Result(ok=False, reason=reason, content_hash=input_hash)

        await self._sem.lock_component_key(session, candidate.component_key)
        component = await self._sem.get_component(session, candidate.component_key)
        if component is None:
            component_id = await self._sem.insert_component(
                session,
                component_key=candidate.component_key,
                cost_budget=cost_budget,
                description=None,
            )
        else:
            component_id = component["id"]

        schema_hash, stable_members = self._semantic_hashes(
            candidate, normalized_ids, specs, cost_budget
        )
        world_schema = await self._sem.get_world_schema_by_hash(
            session, component_id=component_id, content_hash=schema_hash
        )
        persisted_hc = {str(spec_id): candidate.h_c[str(spec_id)] for spec_id in normalized_ids}
        stable_resolution_map = {
            member["spec_hash"]: member["h_c"] for member in stable_members
        }
        if world_schema is None:
            world_schema_id = await self._sem.insert_world_schema(
                session,
                component_id=component_id,
                variables=candidate.variables,
                domains=candidate.domains,
                constraints=candidate.constraints,
                factorization=candidate.factorization,
                world_states=[state.model_dump(mode="json") for state in candidate.world_states],
                state_count=candidate.state_count,
                resolution_map=stable_resolution_map,
                h_c=persisted_hc,
                status="active",
                content_hash=schema_hash,
                schema_version=candidate.schema_version,
            )
        else:
            world_schema_id = world_schema["id"]

        component_hash = canonical_hash(
            {
                "component_key": candidate.component_key,
                "schema_hash": schema_hash,
                "members": stable_members,
                "cost_budget": cost_budget,
            }
        )
        component_version = await self._sem.get_component_version_by_hash(
            session, component_id=component_id, content_hash=component_hash
        )
        if component_version is None:
            component_version_id = await self._sem.insert_component_version(
                session,
                component_id=component_id,
                world_schema_version_id=world_schema_id,
                status="active",
                content_hash=component_hash,
                cost_budget=cost_budget,
            )
            for spec_id in normalized_ids:
                spec_hash = specs[spec_id]["content_hash"]
                hc = candidate.h_c[str(spec_id)]
                await self._sem.insert_component_member(
                    session,
                    component_version_id=component_version_id,
                    contract_spec_id=spec_id,
                    h_c=hc,
                    totality_test_hash=canonical_hash(
                        {"schema_hash": schema_hash, "spec_hash": spec_hash, "h_c": hc}
                    ),
                )
        else:
            component_version_id = component_version["id"]
            existing_members = await self._sem.component_members(session, component_version_id)
            existing_shape = [
                {
                    "spec_hash": row["content_hash"],
                    "resolution_states": sorted(row["kc_resolution_states"]),
                    "h_c": row["h_c"],
                }
                for row in existing_members
            ]
            if existing_shape != stable_members:
                raise RuntimeError("g2_component_idempotency_conflict")

        await self._record_gate(
            session,
            opportunity_id=opportunity_id,
            input_hash=input_hash,
            policy_hash=policy_hash,
            version_manifest_id=version_manifest_id,
            ok=True,
            reason=None,
        )
        return G2Result(
            ok=True,
            component_version_id=component_version_id,
            content_hash=component_hash,
        )

    def _validate(
        self,
        candidate: WorldSchemaInput,
        spec_ids: list[int],
        member_hc: dict[int, dict[str, str]] | None,
        specs: dict[int, dict[str, Any]] | None = None,
    ) -> str | None:
        if not spec_ids or len(spec_ids) != len(set(spec_ids)):
            return G2_COMPONENT_EMPTY
        forbidden = _forbidden_in(candidate.model_dump(mode="json"), "schema")
        if forbidden:
            return f"{G2_SCHEMA_FORBIDDEN}:{forbidden}"
        if candidate.state_count > WORLD_STATE_BUDGET:
            return G2_STATE_BUDGET
        if len(candidate.world_states) != candidate.state_count:
            return G2_CONSTRAINT_CONFLICT

        world_ids = [state.world_state_id for state in candidate.world_states]
        try:
            valid_assignments = _finite_assignments(candidate)
        except OverflowError:
            return G2_STATE_BUDGET
        except ValueError:
            return G2_CONSTRAINT_CONFLICT
        explicit_assignments = [state.assignment for state in candidate.world_states]
        explicit_keys = [canonical_bytes(item) for item in explicit_assignments]
        valid_keys = [canonical_bytes(item) for item in valid_assignments]
        if (
            len(explicit_keys) != len(set(explicit_keys))
            or len(valid_keys) != candidate.state_count
            or set(explicit_keys) != set(valid_keys)
        ):
            return G2_CONSTRAINT_CONFLICT

        expected_keys = {str(spec_id) for spec_id in spec_ids}
        if set(candidate.h_c) != expected_keys:
            return G2_HC_NOT_TOTAL
        if member_hc is not None:
            normalized = {str(key): value for key, value in member_hc.items()}
            if normalized != candidate.h_c:
                return G2_HC_NOT_TOTAL

        if specs is None:
            # Pure unit tests may supply only structural data; DB-bound run_g2 always supplies specs.
            resolution_sets = {spec_id: set(KNOWN_STATES) for spec_id in spec_ids}
        else:
            if set(specs) != set(spec_ids):
                return G2_SPEC_NOT_PASS
            if any(spec["status"] != "pass" for spec in specs.values()):
                return G2_SPEC_NOT_PASS
            resolution_sets = {
                spec_id: set(specs[spec_id]["kc_resolution_states"])
                for spec_id in spec_ids
            }

        for spec_id in spec_ids:
            hc = candidate.h_c.get(str(spec_id))
            if not hc or set(hc) != set(world_ids):
                return G2_HC_NOT_TOTAL
            for state in hc.values():
                if (
                    state in FORBIDDEN_STATES
                    or state not in KNOWN_STATES
                    or state not in resolution_sets[spec_id]
                ):
                    return G2_HC_BAD_STATE
        return G2_OK

    def _semantic_hashes(
        self,
        candidate: WorldSchemaInput,
        spec_ids: list[int],
        specs: dict[int, dict[str, Any]],
        cost_budget: Decimal | int | None,
    ) -> tuple[str, list[dict[str, Any]]]:
        stable_members = sorted(
            (
                {
                    "spec_hash": specs[spec_id]["content_hash"],
                    "resolution_states": sorted(specs[spec_id]["kc_resolution_states"]),
                    "h_c": candidate.h_c[str(spec_id)],
                }
                for spec_id in spec_ids
            ),
            key=lambda item: item["spec_hash"],
        )
        schema_hash = canonical_hash(
            {
                "component_key": candidate.component_key,
                "variables": candidate.variables,
                "domains": candidate.domains,
                "constraints": candidate.constraints,
                "factorization": candidate.factorization,
                "world_states": sorted(
                    (state.model_dump(mode="json") for state in candidate.world_states),
                    key=lambda state: state["world_state_id"],
                ),
                "state_count": candidate.state_count,
                "schema_version": candidate.schema_version,
                "members": stable_members,
                "cost_budget": cost_budget,
            }
        )
        return schema_hash, stable_members

    async def _record_gate(
        self,
        session,
        *,
        opportunity_id: int,
        input_hash: str,
        policy_hash: str,
        version_manifest_id: int,
        ok: bool,
        reason: str | None,
    ) -> None:
        await self._workflow.insert_gate_decision(
            session,
            gate="G2",
            target_kind="opportunity",
            target_id=opportunity_id,
            input_hash=input_hash,
            policy_hash=policy_hash,
            version_manifest_id=version_manifest_id,
            result="PASS" if ok else "FAIL",
            reason_code=reason,
            committed_at=datetime.now(timezone.utc),
        )
