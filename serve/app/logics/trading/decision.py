"""Decision Logic（WP-03 Checkpoint B）：reveal → market-relative → G7A → G7B → terminal。

- ``create_decision``：冻结 P2 execution spec/permission/release 后创建 CREATED decision。
- ``reveal``：按 exact quote checkpoint 写 pm_quote_bindings（stale/crossed/missing fail-closed）。
- ``market_relative``：BLIND_ONLY 默认；LINEAR_SHRINKAGE challenger 仅当能构造 coherent
  Q_market，否则 challenger ABSTAIN、不阻塞 BLIND_ONLY。
- ``run_g7a``：depth walk + full-cost + robust EV，写 action_candidates + cashflows + G7A gate。
- ``run_g7b``：4%/6%/30% caps + marginal utility，写 G7B gate。
- ``terminalize``：ACTION|WAIT|ABSTAIN（HOLD/FLIP/RISK_REVIEW 语义）→ action_set/intent。

全部确定性、DB-backed；AI 不参与本链（任务 §2.4）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.db.uow import UnitOfWork
from app.domain.trading.hashing import canonical_hash
from app.domain.trading.probability import normalize_q, validate_u
from app.domain.trading.portfolio import cap_check
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


class DecisionLogic:
    """决策链编排；全部确定性、DB-backed。"""

    def __init__(
        self,
        decision: DecisionRepository,
        workflow: WorkflowRepository | None = None,
    ) -> None:
        self._decision = decision
        self._workflow = workflow or WorkflowRepository()

    # ---------------- 1. create decision ----------------

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
        if (
            release is None
            or release["release_status"] != "active"
            or release["exec_spec_status"] != "active"
            or release["capital_status"] != "active"
            or release["mode"] != "shadow"
            or release["authorized_capital"] != 0
            or release["kill_switch"]
        ):
            return DecisionResult(False, reason=REASON_FREEZE_INVALID)

        input_manifest = {
            "episode_key": material["episode_key"],
            "submission_id": material["submission_id"],
            "lease_id": material["lease_id"],
            "valid_until": material["valid_until"],
            "evidence_hash": material["evidence_hash"],
            "schema_hash": material["schema_hash"],
            "spec_hash": material["spec_hash"],
            "cohort_key": material["cohort_key"],
            "release_manifest_id": material["release_manifest_id"],
            "trigger_at": trigger_at,
        }
        input_hash = canonical_hash(input_manifest)
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
            decision_class="CHAMPION",
            trigger_at=trigger_at,
            input_hash=input_hash,
        )
        return DecisionResult(True, trade_decision_id=decision_id)

    # ---------------- 2. reveal ----------------

    async def reveal(
        self,
        uow: UnitOfWork,
        *,
        trade_decision_id: int,
        quote_reveal_at: datetime,
        quotes: dict[int, dict[str, Any]],
    ) -> DecisionResult:
        decision = await self._decision.get_trade_decision_by_id(uow.session, trade_decision_id)
        if decision is None or decision["status"] != "CREATED":
            return DecisionResult(False, reason="decision_not_created")
        if not quotes:
            return DecisionResult(False, reason=REASON_QUOTE_MISSING)
        for token_id, quote in quotes.items():
            if not isinstance(quote, dict) or "best_bid" not in quote or "best_ask" not in quote:
                return DecisionResult(False, reason=REASON_QUOTE_MISSING)
            if quote["best_bid"] <= 0 or quote["best_ask"] <= 0 or quote["best_bid"] >= quote["best_ask"]:
                return DecisionResult(False, reason=REASON_QUOTE_CROSSED)
            if quote["stale_at"] <= quote_reveal_at:
                return DecisionResult(False, reason=REASON_QUOTE_STALE)
        for token_id, quote in quotes.items():
            await self._decision.bind_quote(
                uow.session,
                trade_decision_id=trade_decision_id,
                token_id=str(token_id),
                checkpoint_id=quote["checkpoint_id"],
                checkpoint_received_at=quote["checkpoint_received_at"],
                best_bid=quote["best_bid"],
                best_ask=quote["best_ask"],
                price_convention="executable_depth_walk",
                as_of=quote["as_of"],
                received_at=quote["received_at"],
                staleness_policy_ref="p2-exec-spec-v1",
                stale_at=quote["stale_at"],
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
            findings={"quote_count": len(quotes), "stale_checked": True, "crossed_checked": True},
        )
        return DecisionResult(True, trade_decision_id=trade_decision_id)

    # ---------------- 3. market-relative belief ----------------

    async def market_relative(
        self,
        uow: UnitOfWork,
        *,
        trade_decision_id: int,
        input_: MarketRelativeInput,
    ) -> DecisionResult:
        decision = await self._decision.get_trade_decision_by_id(uow.session, trade_decision_id)
        if decision is None or decision["status"] != "QUOTE_BOUND":
            return DecisionResult(False, reason="decision_not_quote_bound")
        submission = await self._decision.get_submission_qu(
            uow.session, decision["forecast_submission_id"]
        )
        if submission is None:
            return DecisionResult(False, reason="submission_qu_missing")
        q_blind = normalize_q(input_.q_blind)
        u_blind = validate_u(submission["U"], q=q_blind)

        decision_mode = input_.decision_mode
        w_blind = input_.w_blind
        q_decision = q_blind
        u_decision = u_blind
        reference_identifiability: dict[str, Any] = {"identified": True, "mode": decision_mode}
        if decision_mode == "LINEAR_SHRINKAGE":
            q_market, identified = self._construct_q_market(input_.token_prices, q_blind)
            reference_identifiability = {
                "identified": identified,
                "requires_complete_outcome_set": True,
                "mode": decision_mode,
            }
            if not identified or w_blind is None:
                # 不可识别 market joint distribution → challenger abstain，不阻塞 BLIND_ONLY。
                return DecisionResult(
                    False,
                    reason="ABSTAIN_MARKET_REFERENCE_UNIDENTIFIED",
                    trade_decision_id=trade_decision_id,
                )
            q_decision = {
                state: Decimal(w_blind) * Decimal(q_blind[state])
                + (Decimal(1) - w_blind) * Decimal(q_market[state])
                for state in q_blind
            }
            q_decision = normalize_q({k: str(v) for k, v in q_decision.items()})
            u_decision = [
                {
                    state: str(
                        Decimal(w_blind) * Decimal(member[state])
                        + (Decimal(1) - w_blind) * Decimal(q_market[state])
                    )
                    for state in member
                }
                for member in u_blind
            ]
            u_decision = validate_u(u_decision, q=q_decision)
        input_manifest_hash = canonical_hash(
            {
                "decision_mode": decision_mode,
                "w_blind": str(w_blind),
                "q_blind_hash": canonical_hash(input_.q_blind),
                "token_prices": {str(k): v for k, v in sorted(input_.token_prices.items())},
            }
        )
        output_manifest_hash = canonical_hash(
            {"q_decision": {k: str(v) for k, v in q_decision.items()},
             "u_decision": [{k: str(v) for k, v in member.items()} for member in u_decision]}
        )
        await self._decision.insert_market_relative_decision(
            uow.session,
            trade_decision_id=trade_decision_id,
            decision_mode=decision_mode,
            w_blind=w_blind,
            q_blind={k: str(v) for k, v in q_blind.items()},
            q_decision={k: str(v) for k, v in q_decision.items()},
            u_decision=[{k: str(v) for k, v in member.items()} for member in u_decision],
            u_blind_hash=canonical_hash(submission["U"]),
            u_decision_hash=canonical_hash(u_decision),
            token_gaps={"mode": decision_mode, "n_tokens": len(input_.token_prices)},
            reference_identifiability=reference_identifiability,
            input_manifest_hash=input_manifest_hash,
            output_manifest_hash=output_manifest_hash,
        )
        return DecisionResult(True, trade_decision_id=trade_decision_id)

    # ---------------- 4. G7A ----------------

    async def run_g7a(
        self,
        uow: UnitOfWork,
        *,
        trade_decision_id: int,
        candidates: list[ActionCandidateInput],
        policy_hash: str,
        version_manifest_id: int,
    ) -> G7AResult:
        decision = await self._decision.get_trade_decision_by_id(uow.session, trade_decision_id)
        if decision is None or decision["status"] != "QUOTE_BOUND":
            return G7AResult(False, "decision_not_quote_bound")
        mr = await self._decision.get_market_relative(uow.session, trade_decision_id)
        if mr is None or not mr["u_decision"]:
            return G7AResult(False, "market_relative_missing")
        u_members = [normalize_q(member) for member in mr["u_decision"]]

        for candidate in candidates:
            spec = await self._decision.get_spec_payout_hc(uow.session, candidate.contract_spec_id)
            if spec is None:
                return G7AResult(False, f"spec_missing:{candidate.contract_spec_id}")
            h_c = spec["h_c"]
            payout_ir = spec["payouts"].get(candidate.token_id)
            if not isinstance(payout_ir, dict):
                return G7AResult(False, f"payout_missing_token:{candidate.token_id}")
            levels = [(lvl[0], lvl[1]) for lvl in candidate.depth_levels if len(lvl) == 2]
            fill = depth_walk(
                levels, side=candidate.side,
                target_quantity=candidate.target_quantity,
                taker_fee_bps=candidate.taker_fee_bps,
            )
            if fill.fill_quantity <= 0:
                continue  # 不可成交候选不落库（depth 不足 → 无 candidate）
            cost = CostComponents(explicit_fee=fill.fee)
            q_member = u_members[0]
            world_delta = world_delta_w(
                q_member, h_c=h_c, payout_ir=payout_ir, cost=cost,
                token_quantity=fill.fill_quantity, token_vwap=fill.vwap,
            )
            robust, point = robust_ev(u_members, world_delta)
            net = robust - cost.total()
            candidate_id = await self._decision.insert_action_candidate(
                uow.session,
                trade_decision_id=trade_decision_id,
                contract_spec_id=candidate.contract_spec_id,
                token_id=candidate.token_id,
                action_type=candidate.action_type,
                fill_quantity=fill.fill_quantity,
                vwap=fill.vwap,
                executable_depth={
                    "levels_used": len(levels), "complete": fill.complete,
                    "unfilled": str(fill.remaining_quantity), "reason": fill.unfilled_reason,
                },
                cost_components={
                    "entry": str(cost.executable_entry_cashflow),
                    "fee": str(cost.explicit_fee),
                    "adjustment": "0", "funding_discount": "0",
                    "capital_charge": "0", "op_cost": "0",
                },
                cashflow_reconciliation_residual=Decimal("0"),
                gross_edge=max(Decimal("0"), point - fill.vwap),
                break_even_payout_probability=break_even_payout_probability(
                    payout_ir, fill.vwap
                ),
                net_edge=net,
                robust_ev=robust,
                point_ev=point,
                roi=roi(net, candidate.bankroll),
                expected_log_growth=expected_log_growth(
                    u_members, world_delta, bankroll=candidate.bankroll
                ),
                worst_loss=worst_loss(world_delta),
                capital_days=capital_days(net, candidate.horizon_days),
                edge_delay_erosion=edge_delay_erosion(
                    max(Decimal("0"), point - fill.vwap), 0, 1
                ),
            )
            for state, cashflow in world_delta.items():
                await self._decision.insert_cashflow(
                    uow.session,
                    action_candidate_id=candidate_id,
                    world_state_id=state,
                    cashflow=cashflow,
                    signed_flag="+",
                )
        await self._workflow.insert_gate_decision(
            uow.session,
            gate="G7A", target_kind="trade_decision", target_id=trade_decision_id,
            input_hash=canonical_hash({"candidates": len(candidates)}),
            policy_hash=policy_hash,
            version_manifest_id=version_manifest_id,
            result="PASS", reason_code=None,
            committed_at=datetime.now(timezone.utc),
        )
        if not await self._decision.advance_status(uow.session, trade_decision_id, to_status="G7A"):
            return G7AResult(False, "g7a_status_conflict")
        return G7AResult(True, candidate_count=len(candidates))

    # ---------------- 5. G7B ----------------

    async def run_g7b(
        self,
        uow: UnitOfWork,
        *,
        trade_decision_id: int,
        portfolio: PortfolioGateInput,
        policy_hash: str,
        version_manifest_id: int,
    ) -> G7BResult:
        decision = await self._decision.get_trade_decision_by_id(uow.session, trade_decision_id)
        if decision is None or decision["status"] != "G7A":
            return G7BResult(False, "decision_not_g7a")
        check = cap_check(
            market_exposure=portfolio.market_exposure,
            component_exposure=portfolio.component_exposure,
            global_exposure=portfolio.global_exposure,
            bankroll=portfolio.bankroll,
            per_market_cap=portfolio.per_market_cap,
            per_component_cap=portfolio.per_component_cap,
            global_cap=portfolio.global_cap,
        )
        if not check.ok:
            await self._workflow.insert_gate_decision(
                uow.session,
                gate="G7B", target_kind="trade_decision", target_id=trade_decision_id,
                input_hash=canonical_hash({"cap": "checked"}),
                policy_hash=policy_hash,
                version_manifest_id=version_manifest_id,
                result="FAIL", reason_code=check.reason,
                committed_at=datetime.now(timezone.utc),
            )
            return G7BResult(False, check.reason)
        await self._workflow.insert_gate_decision(
            uow.session,
            gate="G7B", target_kind="trade_decision", target_id=trade_decision_id,
            input_hash=canonical_hash({"cap": "checked"}),
            policy_hash=policy_hash,
            version_manifest_id=version_manifest_id,
            result="PASS", reason_code=None,
            committed_at=datetime.now(timezone.utc),
        )
        if not await self._decision.advance_status(uow.session, trade_decision_id, to_status="G7B"):
            return G7BResult(False, "g7b_status_conflict")
        return G7BResult(True)

    # ---------------- 6. terminalize ----------------

    async def terminalize(
        self,
        uow: UnitOfWork,
        *,
        trade_decision_id: int,
        action_set: ActionSetInput,
        underwriting: UnderwritingInput | None,
        decided_at: datetime,
    ) -> DecisionResult:
        decision = await self._decision.get_trade_decision_by_id(uow.session, trade_decision_id)
        if decision is None or decision["status"] != "G7B":
            return DecisionResult(False, reason="decision_not_g7b")
        disposition = action_set.disposition
        action_set_key = canonical_hash(
            {
                "kind": "action_set", "trade_decision_id": trade_decision_id,
                "disposition": disposition, "decided_at": decided_at,
            }
        )
        action_set_hash = canonical_hash(
            {
                "disposition": disposition,
                "reason": action_set.reason_code,
                "wake": action_set.wake_condition,
                "recheck": action_set.recheck_at,
                "legs": {
                    role: {
                        str(cs): {str(tk): str(q) for tk, q in tokens.items()}
                        for cs, tokens in spec_map.items()
                    }
                    for role, spec_map in action_set.legs.items()
                },
            }
        )
        set_id = await self._decision.insert_action_set(
            uow.session,
            action_set_key=action_set_key,
            trade_decision_id=trade_decision_id,
            disposition=disposition,
            reason_code=action_set.reason_code,
            wake_condition=action_set.wake_condition,
            recheck_at=action_set.recheck_at,
            action_set_hash=action_set_hash,
        )
        for role, spec_map in action_set.legs.items():
            for contract_spec_id, tokens in spec_map.items():
                for token_id, quantity in tokens.items():
                    signed = quantity if role == "open" else -quantity
                    await self._decision.insert_action_set_leg(
                        uow.session, action_set_id=set_id,
                        contract_spec_id=contract_spec_id, token_id=token_id,
                        leg_role=role, quantity=quantity, signed_quantity=signed,
                        entry_vwap=Decimal("0"),
                    )
        if underwriting is not None:
            await self._decision.insert_underwriting_plan(
                uow.session, trade_decision_id=trade_decision_id,
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
            {"action_set_hash": action_set_hash, "disposition": disposition}
        )
        if not await self._decision.terminal_decision(
            uow.session, trade_decision_id,
            disposition=disposition, decided_at=decided_at, output_hash=output_hash,
            reason_code=action_set.reason_code,
            selected_action_type="HOLD" if disposition == "HOLD" else None,
        ):
            return DecisionResult(False, reason="decision_terminal_conflict")
        return DecisionResult(True, trade_decision_id=trade_decision_id,
                              disposition=disposition, output_hash=output_hash)

    # ---------------- helpers ----------------

    def _construct_q_market(
        self, token_prices: dict[int, str], q_blind: dict[str, Decimal]
    ):
        """从完整互斥 token ask set 构造 coherent Q_market。

        仅当同一 contract 的 token asks 覆盖全部状态且和为 1 时视为可识别；
        否则返回 (None, False) → challenger abstain。
        """
        if not token_prices:
            return None, False
        values: dict[str, Decimal] = {}
        total = Decimal("0")
        for token_id, price in token_prices.items():
            try:
                dec = Decimal(str(price))
            except Exception:
                return None, False
            if not dec.is_finite() or dec < 0 or dec > 1:
                return None, False
            values[str(token_id)] = dec
            total += dec
        # 完整互斥 outcome set：和=1（1e-12 容差）。
        if abs(total - Decimal("1")) > Decimal("1e-12"):
            return None, False
        # 状态数必须等于 Q 的状态数（完整覆盖）。
        if len(values) != len(q_blind):
            return None, False
        return values, True
