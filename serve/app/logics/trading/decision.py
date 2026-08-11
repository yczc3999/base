"""Authoritative, deterministic WP-03 decision chain."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from app.db.uow import UnitOfWork
from app.domain.trading.hashing import canonical_hash
from app.domain.trading.probability import normalize_q, validate_u
from app.domain.trading.rounding import round_cash
from app.domain.trading.valuation import (
    CostComponents,
    break_even_payout_probability,
    capital_days,
    depth_walk,
    edge_delay_erosion,
    expected_log_growth,
    robust_ev,
    roi,
    world_delta_w,
    worst_loss,
)
from app.logics.trading.portfolio import PortfolioLogic
from app.repositories.trading.decision import DecisionRepository
from app.repositories.trading.workflow import WorkflowRepository
from app.schemas.trading.decision import (
    ActionCandidateInput,
    ActionSetInput,
    MarketRelativeInput,
    PortfolioGateInput,
    UnderwritingInput,
)


REASON_EPISODE_NOT_COMMITTED = "decision_episode_not_blind_committed"
REASON_LEASE_INVALID = "decision_lease_invalid"
REASON_FREEZE_INVALID = "decision_freeze_invalid"
REASON_QUOTE_MISSING = "decision_quote_missing"
REASON_QUOTE_STALE = "decision_quote_stale"
REASON_QUOTE_CROSSED = "decision_quote_crossed"
EXPOSURE_ACTIONS = {"BUY_TOKEN", "ADD_TOKEN", "FLIP"}
RISK_REDUCING_ACTIONS = {"SELL_TOKEN_TO_REDUCE", "SELL_TOKEN_TO_CLOSE"}

# Declarative code manifest for the stable business-identity derivation below.
# Persisting this hash in the material makes an intentional algorithm change
# distinguishable from sequence/order drift in replay evidence.
ACTION_IDENTITY_HASH_ALGORITHM = {
    "version": "decision-action-identity/v2",
    "episode": "episode_key",
    "submission": "submission_key",
    "contract_version": ["contract_key", "contract_spec_hash"],
    "token": "external_token_id",
    "leg_fields": ["role", "quantity"],
    "action_set_fields": ["action_type", "vwap"],
    "ordering": "lexicographic/stable-leg-fields/v1",
}
ACTION_IDENTITY_HASH_ALGORITHM_CODE_HASH = canonical_hash(
    ACTION_IDENTITY_HASH_ALGORITHM
)


@dataclass(frozen=True)
class DecisionResult:
    ok: bool
    trade_decision_id: int | None = None
    disposition: str | None = None
    reason: str | None = None
    output_hash: str | None = None


@dataclass(frozen=True)
class G7AResult:
    ok: bool
    reason: str | None = None
    candidate_count: int = 0


@dataclass(frozen=True)
class G7BResult:
    ok: bool
    reason: str | None = None


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError("decision_decimal_invalid")
    return Decimal(str(value))


def _same_decimal(left: Any, right: Any) -> bool:
    try:
        return _decimal(left) == _decimal(right)
    except Exception:
        return False


class DecisionLogic:
    """The caller chooses an opportunity/action size, never its evidence or limits."""

    def __init__(
        self,
        decision: DecisionRepository,
        workflow: WorkflowRepository | None = None,
        portfolio: PortfolioLogic | None = None,
    ) -> None:
        self._decision = decision
        self._workflow = workflow or WorkflowRepository()
        self._portfolio = portfolio or PortfolioLogic()

    async def create_decision(
        self,
        uow: UnitOfWork,
        *,
        episode_id: int,
        trigger_at: datetime,
        experiment_variant: str,
    ) -> DecisionResult:
        material = await self._decision.decision_material(uow.session, episode_id)
        if material is None or material["episode_status"] != "BLIND_COMMITTED":
            return DecisionResult(False, reason=REASON_EPISODE_NOT_COMMITTED)
        if material["lease_id"] is None or material["valid_until"] <= trigger_at:
            return DecisionResult(False, reason=REASON_LEASE_INVALID)
        release = await self._decision.release_by_episode(uow.session, episode_id)
        if not self._release_authorized(release):
            return DecisionResult(False, reason=REASON_FREEZE_INVALID)
        markets = await self._decision.episode_markets(uow.session, episode_id)
        if not markets or any(not self._market_tradeable(row, trigger_at) for row in markets):
            return DecisionResult(False, reason="decision_market_terminal")
        cognition_reviewed = await self._decision.cognition_review_passed(
            uow.session,
            episode_id=episode_id,
            forecast_submission_id=material["submission_id"],
        )

        input_manifest = {
            "episode_key": material["episode_key"],
            "forecast_submission": {
                "submission_key": material["submission_key"],
                "forecast_input_manifest_hash": material[
                    "forecast_input_manifest_hash"
                ],
                "contract_schema_prior_evidence_hash": material[
                    "contract_schema_prior_evidence_hash"
                ],
                "algorithm_hash": material["algorithm_hash"],
            },
            "forecast_lease": {
                "valid_until": material["valid_until"],
                "evidence_hash": material["evidence_hash"],
                "schema_hash": material["schema_hash"],
                "spec_hash": material["spec_hash"],
            },
            "cohort_key": material["cohort_key"],
            "release_name": release.get("release_name"),
            "release_hash": release.get("release_hash"),
            "exec_spec_hash": release.get("exec_spec_hash"),
            "capital_hash": release.get("capital_hash"),
            "trigger_at": trigger_at,
        }
        decision_key = canonical_hash(
            {
                "kind": "trade_decision",
                "episode_key": material["episode_key"],
                "trigger_at": trigger_at,
                "variant": experiment_variant,
            }
        )
        decision_id = await self._decision.insert_trade_decision(
            uow.session,
            decision_key=decision_key,
            episode_id=episode_id,
            forecast_submission_id=material["submission_id"],
            forecast_lease_id=material["lease_id"],
            objective_contract_id=material["objective_contract_id"],
            strategy_version_id=material["strategy_version_id"],
            release_manifest_id=material["release_manifest_id"],
            execution_spec_version_id=release["execution_spec_version_id"],
            capital_permission_manifest_id=release["capital_permission_manifest_id"],
            experiment_variant=experiment_variant,
            decision_class="CHAMPION" if cognition_reviewed else "RISK_REVIEW",
            trigger_at=trigger_at,
            input_hash=canonical_hash(input_manifest),
        )
        return DecisionResult(True, trade_decision_id=decision_id)

    async def reveal(
        self,
        uow: UnitOfWork,
        *,
        trade_decision_id: int,
        quote_reveal_at: datetime,
        quotes: dict[Any, dict[str, Any]],
    ) -> DecisionResult:
        context = await self._decision.decision_context(uow.session, trade_decision_id)
        if context is None or context["status"] != "CREATED":
            return DecisionResult(False, reason="decision_not_created")
        reason = self._authority_reason(context, quote_reveal_at)
        if reason:
            return DecisionResult(False, reason=reason)
        required = await self._decision.required_tokens_for_decision(
            uow.session, trade_decision_id
        )
        required_by_external = {str(row["external_token_id"]): row for row in required}
        supplied = {str(key): value for key, value in quotes.items()}
        if not supplied or set(supplied) != set(required_by_external):
            return DecisionResult(False, reason=REASON_QUOTE_MISSING)

        spec = context["exec_spec_content"]
        ttl_seconds = self._required_nonnegative(
            spec, ("staleness", "ttl_seconds"), "decision_staleness_policy_missing"
        )
        max_age = self._required_nonnegative(
            spec, ("staleness", "max_quote_age_seconds"),
            "decision_staleness_policy_missing",
        )
        authoritative: list[tuple[str, dict[str, Any], datetime]] = []
        for external_token_id in sorted(required_by_external):
            quote = supplied[external_token_id]
            if not isinstance(quote, dict):
                return DecisionResult(False, reason=REASON_QUOTE_MISSING)
            if "checkpoint_id" not in quote or "checkpoint_received_at" not in quote:
                return DecisionResult(False, reason=REASON_QUOTE_MISSING)
            checkpoint = await self._decision.checkpoint_material(
                uow.session,
                external_token_id=external_token_id,
                checkpoint_id=quote["checkpoint_id"],
                checkpoint_received_at=quote["checkpoint_received_at"],
            )
            if checkpoint is None or not checkpoint["completeness"] or checkpoint["validity"] != "VALID":
                return DecisionResult(False, reason=REASON_QUOTE_MISSING)
            bid, ask = checkpoint["best_bid"], checkpoint["best_ask"]
            if bid is None or ask is None or bid <= 0 or ask <= 0 or bid >= ask:
                return DecisionResult(False, reason=REASON_QUOTE_CROSSED)
            received = checkpoint["received_at"]
            stale_at = received + timedelta(seconds=float(ttl_seconds))
            if stale_at <= quote_reveal_at or (quote_reveal_at - received).total_seconds() > float(max_age):
                return DecisionResult(False, reason=REASON_QUOTE_STALE)
            compatibility = {
                "best_bid": bid,
                "best_ask": ask,
                "as_of": received,
                "received_at": received,
                "stale_at": stale_at,
            }
            for key, value in compatibility.items():
                if key not in quote:
                    continue
                same = _same_decimal(quote[key], value) if key in {"best_bid", "best_ask"} else quote[key] == value
                if not same:
                    return DecisionResult(False, reason=f"decision_quote_authority_mismatch:{key}")
            authoritative.append((external_token_id, checkpoint, stale_at))

        for external_token_id, checkpoint, stale_at in authoritative:
            await self._decision.bind_quote(
                uow.session,
                trade_decision_id=trade_decision_id,
                token_id=external_token_id,
                checkpoint_id=checkpoint["checkpoint_id"],
                checkpoint_received_at=checkpoint["received_at"],
                best_bid=checkpoint["best_bid"],
                best_ask=checkpoint["best_ask"],
                price_convention=context["exec_spec_content"]["price_convention"],
                as_of=checkpoint["received_at"],
                received_at=checkpoint["received_at"],
                staleness_policy_ref=context["exec_spec_hash"],
                stale_at=stale_at,
            )
        if not await self._decision.mark_quote_bound(
            uow.session, trade_decision_id, quote_bound_at=quote_reveal_at
        ):
            return DecisionResult(False, reason="decision_quote_bound_conflict")
        await self._decision.insert_discrepancy_review(
            uow.session,
            trade_decision_id=trade_decision_id,
            review_key=f"reveal-{quote_reveal_at.isoformat()}",
            kind="book_integrity",
            result="PASS",
            reason_code=None,
            findings={
                "quote_count": len(authoritative),
                "exact_token_set": True,
                "checkpoint_authoritative": True,
                "exec_spec_hash": context["exec_spec_hash"],
            },
        )
        return DecisionResult(True, trade_decision_id=trade_decision_id)

    async def market_relative(
        self,
        uow: UnitOfWork,
        *,
        trade_decision_id: int,
        input_: MarketRelativeInput,
    ) -> DecisionResult:
        context = await self._decision.decision_context(uow.session, trade_decision_id)
        if context is None or context["status"] != "QUOTE_BOUND":
            return DecisionResult(False, reason="decision_not_quote_bound")
        submission = await self._decision.get_submission_qu(
            uow.session, context["forecast_submission_id"]
        )
        if submission is None:
            return DecisionResult(False, reason="submission_qu_missing")
        q_blind = normalize_q(submission["Q"])
        u_blind = validate_u(submission["U"], q=q_blind)
        if input_.q_blind is not None and normalize_q(input_.q_blind) != q_blind:
            return DecisionResult(False, reason="decision_q_blind_authority_mismatch")

        bound = await self._decision.bound_market_material(uow.session, trade_decision_id)
        required = await self._decision.required_tokens_for_decision(
            uow.session, trade_decision_id
        )
        if {row["external_token_id"] for row in bound} != {
            row["external_token_id"] for row in required
        }:
            return DecisionResult(False, reason=REASON_QUOTE_MISSING)
        by_internal = {row["token_id"]: row for row in bound}
        for token_id, supplied_price in input_.token_prices.items():
            if token_id not in by_internal or not _same_decimal(
                supplied_price, by_internal[token_id]["best_ask"]
            ):
                return DecisionResult(False, reason="decision_market_price_authority_mismatch")

        mode = input_.decision_mode
        w_blind: Decimal | None = None
        q_decision = q_blind
        u_decision = u_blind
        reference: dict[str, Any] = {"identified": mode == "BLIND_ONLY", "mode": mode}
        if mode == "LINEAR_SHRINKAGE":
            policy = self._shrinkage_policy(context)
            if not policy or not policy.get("enabled") or policy.get("w_blind") is None:
                return DecisionResult(
                    False,
                    trade_decision_id=trade_decision_id,
                    reason="ABSTAIN_MARKET_REFERENCE_UNIDENTIFIED",
                )
            w_blind = _decimal(policy["w_blind"])
            if input_.w_blind is not None and input_.w_blind != w_blind:
                return DecisionResult(False, reason="decision_shrinkage_weight_authority_mismatch")
            q_market, identified = self._construct_q_market(bound, q_blind)
            reference = {
                "identified": identified,
                "constructor": "one_hot_complete_outcome_ask/v1",
                "mode": mode,
            }
            if not identified or q_market is None:
                return DecisionResult(
                    False,
                    trade_decision_id=trade_decision_id,
                    reason="ABSTAIN_MARKET_REFERENCE_UNIDENTIFIED",
                )
            q_decision = normalize_q(
                {
                    state: str(w_blind * q_blind[state] + (Decimal("1") - w_blind) * q_market[state])
                    for state in q_blind
                }
            )
            u_decision = validate_u(
                [
                    {
                        state: str(
                            w_blind * member[state]
                            + (Decimal("1") - w_blind) * q_market[state]
                        )
                        for state in member
                    }
                    for member in u_blind
                ],
                q=q_decision,
            )

        input_manifest_hash = canonical_hash(
            {
                "mode": mode,
                "submission_q_hash": canonical_hash(submission["Q"]),
                "submission_u_hash": canonical_hash(submission["U"]),
                "quote_bindings": [
                    {
                        "token": row["external_token_id"],
                        "checkpoint": row["checkpoint_id"],
                        "ask": str(row["best_ask"]),
                    }
                    for row in bound
                ],
                "exec_spec_hash": context["exec_spec_hash"],
            }
        )
        serialized_q = {key: str(value) for key, value in q_decision.items()}
        serialized_u = [
            {key: str(value) for key, value in member.items()} for member in u_decision
        ]
        output_manifest_hash = canonical_hash(
            {"q_decision": serialized_q, "u_decision": serialized_u}
        )
        await self._decision.insert_market_relative_decision(
            uow.session,
            trade_decision_id=trade_decision_id,
            decision_mode=mode,
            w_blind=w_blind,
            q_blind={key: str(value) for key, value in q_blind.items()},
            q_decision=serialized_q,
            u_decision=serialized_u,
            u_blind_hash=canonical_hash(submission["U"]),
            u_decision_hash=canonical_hash(serialized_u),
            token_gaps={"mode": mode, "n_tokens": len(bound)},
            reference_identifiability=reference,
            input_manifest_hash=input_manifest_hash,
            output_manifest_hash=output_manifest_hash,
        )
        return DecisionResult(True, trade_decision_id=trade_decision_id)

    async def run_g7a(
        self,
        uow: UnitOfWork,
        *,
        trade_decision_id: int,
        candidates: list[ActionCandidateInput],
        policy_hash: str | None = None,
        version_manifest_id: int | None = None,
    ) -> G7AResult:
        context = await self._decision.decision_context(uow.session, trade_decision_id)
        if context is None or context["status"] != "QUOTE_BOUND":
            return G7AResult(False, "decision_not_quote_bound")
        if policy_hash is not None and policy_hash != context["exec_spec_hash"]:
            return G7AResult(False, "g7a_policy_binding_mismatch")
        if version_manifest_id is not None and version_manifest_id != context["release_manifest_id"]:
            return G7AResult(False, "g7a_release_binding_mismatch")
        mr = await self._decision.get_market_relative(uow.session, trade_decision_id)
        if mr is None or not mr["u_decision"]:
            return G7AResult(False, "market_relative_missing")
        q_decision = normalize_q(mr["q_decision"])
        u_members = [normalize_q(member) for member in mr["u_decision"]]
        bound = await self._decision.bound_market_material(uow.session, trade_decision_id)
        by_key = {(row["contract_spec_id"], row["token_id"]): row for row in bound}
        cost_evidence = await self._decision.allocated_operating_cost(
            uow.session, trade_decision_id
        )
        if cost_evidence is None:
            return await self._g7a_fail(
                uow, trade_decision_id, context, candidates, "g7a_operating_cost_evidence_missing"
            )

        spec = context["exec_spec_content"]
        fee_bps = self._required_nonnegative(spec, ("fee", "taker_fee_bps"), "g7a_fee_policy_missing")
        slippage_bps = self._required_nonnegative(spec, ("slippage", "bps"), "g7a_slippage_policy_missing")
        minimum_capacity = self._required_nonnegative(
            spec, ("capacity", "minimum_deployable_capacity"), "g7a_capacity_policy_missing"
        )
        maximum_units = self._required_nonnegative(
            spec, ("capacity", "maximum_single_leg_units"), "g7a_capacity_policy_missing"
        )
        max_levels = int(self._required_nonnegative(spec, ("depth_walk", "max_levels"), "g7a_depth_policy_missing"))
        safety_margin = _decimal(spec.get("capacity", {}).get("safety_margin", "0"))
        bankroll = _decimal(context["evaluation_capital"])
        if bankroll <= 0:
            return await self._g7a_fail(uow, trade_decision_id, context, candidates, "g7a_evaluation_capital_missing")

        inserted = 0
        rejection: str | None = None
        for request in candidates:
            row = by_key.get((request.contract_spec_id, request.token_id))
            if row is None:
                rejection = f"g7a_candidate_not_bound:{request.contract_spec_id}:{request.token_id}"
                continue
            if request.action_type == "HOLD" or request.action_type not in EXPOSURE_ACTIONS | RISK_REDUCING_ACTIONS:
                rejection = f"g7a_action_unsupported:{request.action_type}"
                continue
            side = "buy" if request.action_type in EXPOSURE_ACTIONS else "sell"
            if request.side is not None and request.side != side:
                rejection = "g7a_side_authority_mismatch"
                continue
            if request.taker_fee_bps is not None and request.taker_fee_bps != fee_bps:
                rejection = "g7a_fee_authority_mismatch"
                continue
            if request.bankroll is not None and request.bankroll != bankroll:
                rejection = "g7a_bankroll_authority_mismatch"
                continue
            horizon_days = Decimal("1")
            if request.horizon_days is not None and request.horizon_days != horizon_days:
                rejection = "g7a_horizon_authority_mismatch"
                continue
            if request.target_quantity > maximum_units:
                rejection = "g7a_maximum_single_leg_exceeded"
                continue
            book_side = "ask" if side == "buy" else "bid"
            levels = [
                (level["price"], level["size"])
                for level in row["levels"]
                if level["side"] == book_side
            ][:max_levels]
            if request.depth_levels:
                supplied = [(Decimal(str(level[0])), Decimal(str(level[1]))) for level in request.depth_levels]
                authoritative = [(Decimal(str(price)), Decimal(str(size))) for price, size in levels]
                if supplied != authoritative:
                    rejection = "g7a_depth_authority_mismatch"
                    continue
            if not levels:
                rejection = "g7a_bound_depth_missing"
                continue
            try:
                fill = depth_walk(
                    levels,
                    side=side,
                    target_quantity=request.target_quantity,
                    taker_fee_bps=fee_bps,
                )
            except ValueError as exc:
                rejection = str(exc)
                continue
            if fill.fill_quantity < minimum_capacity or (
                request.action_type in EXPOSURE_ACTIONS and not fill.complete
            ):
                rejection = "g7a_minimum_or_complete_capacity_failed"
                continue

            entry = round_cash(fill.fill_quantity * fill.vwap)
            execution_adjustment = round_cash(entry * slippage_bps / Decimal("10000"))
            operating_cost = _decimal(cost_evidence["amount"])
            cost = CostComponents(
                explicit_fee=fill.fee,
                execution_adjustment=execution_adjustment,
                funding_or_discount_adjustment=Decimal("0"),
                capital_charge=Decimal("0"),
                allocated_marginal_operating_cost=operating_cost,
            )
            world_delta = world_delta_w(
                q_decision,
                h_c=row["h_c"],
                payout_ir=row["payout_ir"],
                cost=cost,
                token_quantity=fill.fill_quantity,
                token_vwap=fill.vwap,
                side=side,
            )
            robust, point = robust_ev(u_members, world_delta, point_q=q_decision)
            if request.action_type in EXPOSURE_ACTIONS and robust <= safety_margin:
                rejection = "g7a_robust_ev_below_margin"
                continue
            non_entry_cost = (
                fill.fee + execution_adjustment + operating_cost
            )
            gross_edge = max(Decimal("0"), point + non_entry_cost)
            capital_employed = max(Decimal("1"), entry + non_entry_cost)
            all_in_per_unit = (entry + non_entry_cost) / fill.fill_quantity
            candidate_id = await self._decision.insert_action_candidate(
                uow.session,
                trade_decision_id=trade_decision_id,
                contract_spec_id=request.contract_spec_id,
                token_id=request.token_id,
                action_type=request.action_type,
                fill_quantity=fill.fill_quantity,
                vwap=fill.vwap,
                executable_depth={
                    "checkpoint_id": row["checkpoint_id"],
                    "checkpoint_received_at": row["checkpoint_received_at"].isoformat(),
                    "side": book_side,
                    "levels": [[str(price), str(size)] for price, size in levels],
                    "complete": fill.complete,
                    "unfilled": str(fill.remaining_quantity),
                    "reason": fill.unfilled_reason,
                },
                cost_components={
                    "entry": str(entry if side == "buy" else -entry),
                    "fee": str(fill.fee),
                    "adjustment": str(execution_adjustment),
                    "funding_discount": "0",
                    "capital_charge": "0",
                    "op_cost": str(operating_cost),
                    "op_cost_evidence_count": cost_evidence["evidence_count"],
                    "baseline_rebate": "0",
                },
                cashflow_reconciliation_residual=Decimal("0"),
                gross_edge=gross_edge,
                break_even_payout_probability=break_even_payout_probability(
                    row["payout_ir"], all_in_per_unit
                ),
                net_edge=robust,
                robust_ev=robust,
                point_ev=point,
                roi=roi(robust, capital_employed),
                expected_log_growth=expected_log_growth(
                    [q_decision], world_delta, bankroll=bankroll
                ),
                worst_loss=worst_loss(world_delta),
                capital_days=capital_days(entry, horizon_days),
                edge_delay_erosion=edge_delay_erosion(gross_edge, 0, 1),
            )
            for state, cashflow in sorted(world_delta.items()):
                await self._decision.insert_cashflow(
                    uow.session,
                    action_candidate_id=candidate_id,
                    world_state_id=state,
                    cashflow=cashflow,
                    signed_flag="+" if cashflow >= 0 else "-",
                )
            inserted += 1

        if inserted == 0:
            return await self._g7a_fail(
                uow,
                trade_decision_id,
                context,
                candidates,
                rejection or "g7a_no_eligible_candidate",
            )
        await self._workflow.insert_gate_decision(
            uow.session,
            gate="G7A",
            target_kind="trade_decision",
            target_id=trade_decision_id,
            input_hash=canonical_hash(
                {
                    "requests": [request.model_dump(mode="json") for request in candidates],
                    "exec_spec_hash": context["exec_spec_hash"],
                    "operating_cost": cost_evidence,
                }
            ),
            policy_hash=context["exec_spec_hash"],
            version_manifest_id=context["release_manifest_id"],
            result="PASS",
            reason_code=None,
            committed_at=datetime.now(timezone.utc),
        )
        if not await self._decision.advance_status(
            uow.session, trade_decision_id, to_status="G7A"
        ):
            return G7AResult(False, "g7a_status_conflict")
        return G7AResult(True, candidate_count=inserted)

    async def run_g7b(
        self,
        uow: UnitOfWork,
        *,
        trade_decision_id: int,
        portfolio: PortfolioGateInput | None = None,
        policy_hash: str | None = None,
        version_manifest_id: int | None = None,
    ) -> G7BResult:
        context = await self._decision.decision_context(uow.session, trade_decision_id)
        if context is None or context["status"] != "G7A":
            return G7BResult(False, "decision_not_g7a")
        if policy_hash is not None and policy_hash != context["exec_spec_hash"]:
            return G7BResult(False, "g7b_policy_binding_mismatch")
        if version_manifest_id is not None and version_manifest_id != context["release_manifest_id"]:
            return G7BResult(False, "g7b_release_binding_mismatch")
        candidates = await self._decision.candidates_for_decision(
            uow.session, trade_decision_id
        )
        if not candidates:
            return await self._g7b_fail(uow, trade_decision_id, context, "g7b_no_g7a_candidate")
        token_rows = await self._decision.required_tokens_for_decision(
            uow.session, trade_decision_id
        )
        tokens = {(row["contract_spec_id"], row["token_id"]): row for row in token_rows}
        bankroll = _decimal(context["evaluation_capital"])
        caps = self._portfolio.frozen_caps(context["limits"])
        namespace = f"shadow-{context['experiment_variant']}"
        if portfolio and portfolio.portfolio_namespace and portfolio.portfolio_namespace != namespace:
            return G7BResult(False, "g7b_namespace_authority_mismatch")

        best_check = None
        best_reason = "g7b_no_candidate_beats_no_action"
        ordered = sorted(
            candidates,
            key=lambda row: (-_decimal(row["robust_ev"] or 0), row["id"]),
        )
        for candidate in ordered:
            if candidate["action_type"] in EXPOSURE_ACTIONS and _decimal(candidate["robust_ev"] or 0) <= 0:
                continue
            token = tokens.get((candidate["contract_spec_id"], candidate["token_id"]))
            if token is None:
                best_reason = "g7b_candidate_token_not_in_episode"
                continue
            entry = abs(_decimal(candidate["cost_components"]["entry"]))
            delta = entry if candidate["action_type"] in EXPOSURE_ACTIONS else -entry
            check = await self._portfolio.check_capacity(
                uow,
                portfolio_namespace=namespace,
                bankroll=bankroll,
                new_market_exposure=delta,
                new_component_exposure=delta,
                new_global_exposure=delta,
                per_market_cap=caps[0],
                per_component_cap=caps[1],
                global_cap=caps[2],
                market_id=token["market_id"],
                component_id=token["component_id"],
                exclude_decision_id=trade_decision_id,
            )
            if check.ok:
                best_check = check
                break
            best_reason = check.reason or best_reason
        if best_check is None:
            return await self._g7b_fail(uow, trade_decision_id, context, best_reason)

        if portfolio is not None:
            assertions = {
                "bankroll": (portfolio.bankroll, best_check.bankroll),
                "per_market_cap": (portfolio.per_market_cap, best_check.per_market_cap),
                "per_component_cap": (portfolio.per_component_cap, best_check.per_component_cap),
                "global_cap": (portfolio.global_cap, best_check.global_cap),
                "market_exposure": (portfolio.market_exposure, best_check.market_exposure),
                "component_exposure": (portfolio.component_exposure, best_check.component_exposure),
                "global_exposure": (portfolio.global_exposure, best_check.global_exposure),
            }
            for name, (supplied, authoritative) in assertions.items():
                if supplied is not None and supplied != authoritative:
                    return G7BResult(False, f"g7b_{name}_authority_mismatch")

        await self._workflow.insert_gate_decision(
            uow.session,
            gate="G7B",
            target_kind="trade_decision",
            target_id=trade_decision_id,
            input_hash=canonical_hash(
                {
                    "namespace": namespace,
                    "bankroll": str(bankroll),
                    "caps": [str(value) for value in caps],
                    "market_exposure": str(best_check.market_exposure),
                    "component_exposure": str(best_check.component_exposure),
                    "global_exposure": str(best_check.global_exposure),
                }
            ),
            policy_hash=context["exec_spec_hash"],
            version_manifest_id=context["release_manifest_id"],
            result="PASS",
            reason_code=None,
            committed_at=datetime.now(timezone.utc),
        )
        if not await self._decision.advance_status(
            uow.session, trade_decision_id, to_status="G7B"
        ):
            return G7BResult(False, "g7b_status_conflict")
        return G7BResult(True)

    async def terminalize(
        self,
        uow: UnitOfWork,
        *,
        trade_decision_id: int,
        action_set: ActionSetInput,
        underwriting: UnderwritingInput | None,
        decided_at: datetime,
    ) -> DecisionResult:
        context = await self._decision.decision_context(
            uow.session, trade_decision_id, for_update=True
        )
        if context is None or context["status"] != "G7B":
            return DecisionResult(False, reason="decision_not_g7b")
        reason = self._authority_reason(context, decided_at)
        if reason:
            return DecisionResult(False, reason=reason)
        cognition_reviewed = await self._decision.review_passed(
            uow.session, trade_decision_id
        )

        disposition = action_set.disposition
        if disposition in {"WAIT", "ABSTAIN"}:
            if action_set.legs:
                return DecisionResult(False, reason="decision_non_action_has_legs")
            if disposition == "WAIT" and not (action_set.wake_condition or action_set.recheck_at):
                return DecisionResult(False, reason="decision_wait_requires_wake")
            if disposition == "ABSTAIN" and not action_set.reason_code:
                return DecisionResult(False, reason="decision_abstain_requires_reason")
            output_hash = canonical_hash(
                {
                    "disposition": disposition,
                    "reason": action_set.reason_code,
                    "wake": action_set.wake_condition,
                    "recheck": action_set.recheck_at,
                }
            )
            if not await self._decision.terminal_decision(
                uow.session,
                trade_decision_id,
                disposition=disposition,
                decided_at=decided_at,
                output_hash=output_hash,
                reason_code=action_set.reason_code,
                selected_action_type=None,
            ):
                return DecisionResult(False, reason="decision_terminal_conflict")
            return DecisionResult(
                True,
                trade_decision_id=trade_decision_id,
                disposition=disposition,
                output_hash=output_hash,
            )

        candidates = await self._decision.candidates_for_decision(
            uow.session, trade_decision_id
        )
        selected_type = action_set.selected_action_type
        if selected_type is None:
            matching_types = {
                row["action_type"] for row in candidates if self._candidate_matches_any_leg(row, action_set)
            }
            if len(matching_types) == 1:
                selected_type = matching_types.pop()
        if selected_type == "HOLD":
            if action_set.legs or underwriting is not None:
                return DecisionResult(False, reason="decision_hold_shape_invalid")
            namespace = f"shadow-{context['experiment_variant']}"
            if not await self._decision.has_position_for_decision(
                uow.session, trade_decision_id, namespace
            ) or not await self._decision.has_valid_underwriting(
                uow.session, trade_decision_id, decided_at
            ):
                return DecisionResult(False, reason="decision_hold_requires_position_underwriting")
            return await self._persist_action(
                uow,
                context=context,
                trade_decision_id=trade_decision_id,
                action_set=action_set,
                underwriting=None,
                decided_at=decided_at,
                selected_type="HOLD",
                selected=[]
            )
        if selected_type not in EXPOSURE_ACTIONS | RISK_REDUCING_ACTIONS or not action_set.legs:
            return DecisionResult(False, reason="decision_selected_action_missing")
        if selected_type in EXPOSURE_ACTIONS and (
            not cognition_reviewed or context.get("decision_class") == "RISK_REVIEW"
        ):
            return DecisionResult(False, reason="decision_unreviewed")

        selected: list[tuple[dict[str, Any], str, Decimal]] = []
        for role, spec_map in action_set.legs.items():
            if role not in {"open", "close", "reduce"}:
                return DecisionResult(False, reason=f"decision_leg_role_invalid:{role}")
            for contract_spec_id, token_map in spec_map.items():
                for token_id, quantity in token_map.items():
                    matches = [
                        row for row in candidates
                        if row["contract_spec_id"] == contract_spec_id
                        and row["token_id"] == token_id
                        and row["action_type"] == selected_type
                    ]
                    if len(matches) != 1 or quantity <= 0 or quantity > matches[0]["fill_quantity"]:
                        return DecisionResult(False, reason="decision_leg_not_g7a_candidate")
                    if selected_type in EXPOSURE_ACTIONS and role != "open":
                        return DecisionResult(False, reason="decision_open_role_mismatch")
                    if selected_type in RISK_REDUCING_ACTIONS and role not in {"close", "reduce"}:
                        return DecisionResult(False, reason="decision_reduce_role_mismatch")
                    selected.append((matches[0], role, quantity))
        if not selected:
            return DecisionResult(False, reason="decision_action_requires_leg")
        if selected_type in EXPOSURE_ACTIONS and underwriting is None:
            return DecisionResult(False, reason="decision_underwriting_required")

        # Final atomic capacity check.  The COMMITTED intent inserted below is the
        # claim that survives this transaction; no separate reservation table exists.
        token_rows = await self._decision.required_tokens_for_decision(
            uow.session, trade_decision_id
        )
        tokens = {(row["contract_spec_id"], row["token_id"]): row for row in token_rows}
        bankroll = _decimal(context["evaluation_capital"])
        caps = self._portfolio.frozen_caps(context["limits"])
        namespace = f"shadow-{context['experiment_variant']}"
        for candidate, _, quantity in selected:
            token = tokens[(candidate["contract_spec_id"], candidate["token_id"])]
            exposure = abs(quantity * _decimal(candidate["vwap"]))
            delta = exposure if selected_type in EXPOSURE_ACTIONS else -exposure
            check = await self._portfolio.check_capacity(
                uow,
                portfolio_namespace=namespace,
                bankroll=bankroll,
                new_market_exposure=delta,
                new_component_exposure=delta,
                new_global_exposure=delta,
                per_market_cap=caps[0],
                per_component_cap=caps[1],
                global_cap=caps[2],
                market_id=token["market_id"],
                component_id=token["component_id"],
                exclude_decision_id=trade_decision_id,
            )
            if not check.ok:
                return DecisionResult(False, reason=check.reason)

        # Business identity must survive a rolled-back insert consuming a
        # PostgreSQL sequence value.  Bind the action to immutable natural keys
        # and version hashes instead of episode/submission/token surrogate IDs.
        # Explicit sorting also makes the identity independent of input/row order.
        intent_material = {
            "identity_algorithm_hash": ACTION_IDENTITY_HASH_ALGORITHM_CODE_HASH,
            "episode_key": context["episode_key"],
            "forecast_submission_key": context["submission_key"],
            "action_type": selected_type,
            "legs": sorted(
                [
                    {
                        "contract_key": row["contract_key"],
                        "contract_spec_hash": row["contract_spec_hash"],
                        "token_id": row["external_token_id"],
                        "role": role,
                        "quantity": str(quantity),
                    }
                    for row, role, quantity in selected
                ],
                key=lambda leg: (
                    leg["contract_key"],
                    leg["contract_spec_hash"],
                    leg["token_id"],
                    leg["role"],
                    leg["quantity"],
                ),
            ),
        }
        intent_hash = canonical_hash(intent_material)
        if await self._decision.get_action_intent_by_hash(uow.session, intent_hash):
            return DecisionResult(False, reason="decision_duplicate_active_intent")
        return await self._persist_action(
            uow,
            context=context,
            trade_decision_id=trade_decision_id,
            action_set=action_set,
            underwriting=underwriting,
            decided_at=decided_at,
            selected_type=selected_type,
            selected=selected,
            intent_hash=intent_hash,
            intent_material=intent_material,
        )

    async def _persist_action(
        self,
        uow: UnitOfWork,
        *,
        context: dict[str, Any],
        trade_decision_id: int,
        action_set: ActionSetInput,
        underwriting: UnderwritingInput | None,
        decided_at: datetime,
        selected_type: str,
        selected: list[tuple[dict[str, Any], str, Decimal]],
        intent_hash: str | None = None,
        intent_material: dict[str, Any] | None = None,
    ) -> DecisionResult:
        action_set_hash = canonical_hash(
            {
                "identity_algorithm_hash": ACTION_IDENTITY_HASH_ALGORITHM_CODE_HASH,
                "disposition": "ACTION",
                "selected_action_type": selected_type,
                "legs": sorted(
                    [
                        {
                            "contract_key": row["contract_key"],
                            "contract_spec_hash": row["contract_spec_hash"],
                            "token_id": row["external_token_id"],
                            "action_type": row["action_type"],
                            "role": role,
                            "quantity": str(quantity),
                            "vwap": str(row["vwap"]),
                        }
                        for row, role, quantity in selected
                    ],
                    key=lambda leg: (
                        leg["contract_key"],
                        leg["contract_spec_hash"],
                        leg["token_id"],
                        leg["action_type"],
                        leg["role"],
                        leg["quantity"],
                        leg["vwap"],
                    ),
                ),
            }
        )
        set_id = await self._decision.insert_action_set(
            uow.session,
            action_set_key=canonical_hash(
                {"kind": "action_set", "decision_key": context["decision_key"]}
            ),
            trade_decision_id=trade_decision_id,
            disposition="ACTION",
            reason_code=action_set.reason_code,
            wake_condition=action_set.wake_condition,
            recheck_at=action_set.recheck_at,
            action_set_hash=action_set_hash,
        )
        for candidate, role, quantity in selected:
            signed = quantity if role == "open" else -quantity
            await self._decision.insert_action_set_leg(
                uow.session,
                action_set_id=set_id,
                contract_spec_id=candidate["contract_spec_id"],
                token_id=candidate["token_id"],
                leg_role=role,
                quantity=quantity,
                signed_quantity=signed,
                entry_vwap=candidate["vwap"],
            )
        if underwriting is not None:
            await self._decision.insert_underwriting_plan(
                uow.session,
                trade_decision_id=trade_decision_id,
                plan_version=underwriting.plan_version,
                entry_range=underwriting.entry_range,
                hold_to_resolution=underwriting.hold_to_resolution,
                thesis_hash=underwriting.thesis_hash,
                invalidation=underwriting.invalidation,
                wake_condition=underwriting.wake_condition,
                edge_close_threshold=underwriting.edge_close_threshold,
                time_stop_at=underwriting.time_stop_at,
            )
        output_hash = canonical_hash(
            {"action_set_hash": action_set_hash, "selected_action_type": selected_type}
        )
        if not await self._decision.terminal_decision(
            uow.session,
            trade_decision_id,
            disposition="ACTION",
            decided_at=decided_at,
            output_hash=output_hash,
            reason_code=action_set.reason_code,
            selected_action_type=selected_type,
        ):
            return DecisionResult(False, reason="decision_terminal_conflict")
        if selected_type != "HOLD":
            assert intent_hash is not None and intent_material is not None
            await self._decision.insert_action_intent(
                uow.session,
                intent_key=f"intent:{intent_hash}",
                intent_hash=intent_hash,
                trade_decision_id=trade_decision_id,
                action_set_id=set_id,
                ttl_at=datetime.now(timezone.utc) + timedelta(days=30),
                preflight={
                    "action_set_hash": action_set_hash,
                    "intent_material": intent_material,
                    "exec_spec_hash": context["exec_spec_hash"],
                    "capital_hash": context["capital_hash"],
                    "portfolio_namespace": f"shadow-{context['experiment_variant']}",
                },
                status="COMMITTED",
            )
        return DecisionResult(
            True,
            trade_decision_id=trade_decision_id,
            disposition="ACTION",
            output_hash=output_hash,
        )

    async def _g7a_fail(
        self,
        uow: UnitOfWork,
        trade_decision_id: int,
        context: dict[str, Any],
        candidates: list[ActionCandidateInput],
        reason: str,
    ) -> G7AResult:
        await self._workflow.insert_gate_decision(
            uow.session,
            gate="G7A",
            target_kind="trade_decision",
            target_id=trade_decision_id,
            input_hash=canonical_hash(
                {"requests": [row.model_dump(mode="json") for row in candidates], "reason": reason}
            ),
            policy_hash=context["exec_spec_hash"],
            version_manifest_id=context["release_manifest_id"],
            result="FAIL",
            reason_code=reason,
            committed_at=datetime.now(timezone.utc),
        )
        return G7AResult(False, reason, 0)

    async def _g7b_fail(
        self,
        uow: UnitOfWork,
        trade_decision_id: int,
        context: dict[str, Any],
        reason: str,
    ) -> G7BResult:
        await self._workflow.insert_gate_decision(
            uow.session,
            gate="G7B",
            target_kind="trade_decision",
            target_id=trade_decision_id,
            input_hash=canonical_hash({"reason": reason}),
            policy_hash=context["exec_spec_hash"],
            version_manifest_id=context["release_manifest_id"],
            result="FAIL",
            reason_code=reason,
            committed_at=datetime.now(timezone.utc),
        )
        return G7BResult(False, reason)

    @staticmethod
    def _candidate_matches_any_leg(row: dict[str, Any], action_set: ActionSetInput) -> bool:
        return any(
            row["contract_spec_id"] in spec_map
            and row["token_id"] in spec_map[row["contract_spec_id"]]
            for spec_map in action_set.legs.values()
        )

    @staticmethod
    def _market_tradeable(row: dict[str, Any], at: datetime) -> bool:
        return bool(
            row.get("active")
            and not row.get("closed")
            and row.get("accepting_orders")
            and row.get("enable_order_book")
            and (row.get("end_date") is None or row["end_date"] > at)
        )

    @staticmethod
    def _release_authorized(release: dict[str, Any] | None) -> bool:
        return bool(
            release
            and release.get("release_status") == "active"
            and release.get("exec_spec_status") == "active"
            and release.get("capital_status") == "active"
            and release.get("mode") == "shadow"
            and _decimal(release.get("authorized_capital", -1)) == 0
            and not release.get("kill_switch")
        )

    @staticmethod
    def _authority_reason(context: dict[str, Any], at: datetime) -> str | None:
        if context.get("submission_status") != "BLIND_COMMITTED":
            return REASON_EPISODE_NOT_COMMITTED
        if context.get("lease_valid_until") is None or context["lease_valid_until"] <= at:
            return REASON_LEASE_INVALID
        if not (
            context.get("release_status") == "active"
            and context.get("exec_spec_status") == "active"
            and context.get("capital_status") == "active"
            and context.get("capital_mode") == "shadow"
            and _decimal(context.get("authorized_capital", -1)) == 0
            and not context.get("kill_switch")
        ):
            return REASON_FREEZE_INVALID
        return None

    @staticmethod
    def _required_nonnegative(content: dict, path: tuple[str, str], reason: str) -> Decimal:
        try:
            value = _decimal(content[path[0]][path[1]])
        except Exception as exc:
            raise ValueError(reason) from exc
        if value < 0:
            raise ValueError(reason)
        return value

    @staticmethod
    def _shrinkage_policy(context: dict[str, Any]) -> dict[str, Any] | None:
        strategy_content = context.get("strategy_content")
        if isinstance(strategy_content, dict) and isinstance(
            strategy_content.get("optional_shrinkage"), dict
        ):
            return strategy_content["optional_shrinkage"]
        content = context.get("exec_spec_content") or {}
        if isinstance(content.get("optional_shrinkage"), dict):
            return content["optional_shrinkage"]
        strategy = content.get("strategy")
        if isinstance(strategy, dict) and isinstance(strategy.get("optional_shrinkage"), dict):
            return strategy["optional_shrinkage"]
        return None

    @staticmethod
    def _construct_q_market(
        bound_rows: list[dict[str, Any]], q_blind: dict[str, Decimal]
    ) -> tuple[dict[str, Decimal] | None, bool]:
        """Map a complete one-hot outcome quote set onto world states."""
        if not bound_rows or not q_blind:
            return None, False
        contract_ids = {row["contract_spec_id"] for row in bound_rows}
        if len(contract_ids) != 1 or len(bound_rows) != len(q_blind):
            return None, False
        result: dict[str, Decimal] = {}
        used_tokens: set[int] = set()
        for state in q_blind:
            winners: list[dict[str, Any]] = []
            for row in bound_rows:
                resolution = row["h_c"].get(state)
                if resolution is None or resolution not in row["payout_ir"]:
                    return None, False
                payout = _decimal(row["payout_ir"][resolution])
                if payout == 1:
                    winners.append(row)
                elif payout != 0:
                    return None, False
            if len(winners) != 1 or winners[0]["token_id"] in used_tokens:
                return None, False
            winner = winners[0]
            used_tokens.add(winner["token_id"])
            price = _decimal(winner["best_ask"])
            if price < 0 or price > 1:
                return None, False
            result[state] = price
        if len(used_tokens) != len(bound_rows):
            return None, False
        if abs(sum(result.values(), Decimal("0")) - Decimal("1")) > Decimal("1e-12"):
            return None, False
        return result, True
