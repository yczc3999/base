"""Decision Repository（WP-03 Checkpoint B）。

只拥有 SQL：trade_decision 生命周期、market-relative belief、quote binding、G7A candidates、
cashflows、action sets/legs、underwriting、intent。绝不 commit、不调用网络、不做业务判断。
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


class DecisionRepository:
    """decision SQL；不持有状态。"""

    # ---------------- trade_decision ----------------

    async def insert_trade_decision(
        self,
        session: AsyncSession,
        *,
        decision_key: str,
        episode_id: int,
        forecast_submission_id: int,
        forecast_lease_id: int | None,
        objective_contract_id: int,
        strategy_version_id: int,
        release_manifest_id: int,
        execution_spec_version_id: int,
        capital_permission_manifest_id: int,
        experiment_variant: str,
        decision_class: str,
        trigger_at: datetime,
        input_hash: str,
    ) -> int:
        result = await session.execute(
            text(
                "INSERT INTO trading.trade_decisions "
                "(decision_key, episode_id, forecast_submission_id, forecast_lease_id, "
                " objective_contract_id, strategy_version_id, release_manifest_id, "
                " execution_spec_version_id, capital_permission_manifest_id, "
                " experiment_variant, decision_class, trigger_at, input_hash) "
                "VALUES (:k, :e, :s, :l, :obj, :strat, :rel, :es, :cap, :v, :cls, :t, :ih) "
                "ON CONFLICT (decision_key) DO NOTHING RETURNING id"
            ),
            {
                "k": decision_key, "e": episode_id, "s": forecast_submission_id, "l": forecast_lease_id,
                "obj": objective_contract_id, "strat": strategy_version_id, "rel": release_manifest_id,
                "es": execution_spec_version_id, "cap": capital_permission_manifest_id,
                "v": experiment_variant, "cls": decision_class, "t": trigger_at, "ih": input_hash,
            },
        )
        inserted = result.scalar_one_or_none()
        if inserted is not None:
            return inserted
        existing = await self.get_trade_decision(session, decision_key)
        if existing is None:
            raise RuntimeError("trade_decision_missing_after_insert")
        return existing["id"]

    async def get_trade_decision(
        self, session: AsyncSession, decision_key: str
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text("SELECT * FROM trading.trade_decisions WHERE decision_key=:k"),
            {"k": decision_key},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def get_trade_decision_by_id(
        self, session: AsyncSession, trade_decision_id: int
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text("SELECT * FROM trading.trade_decisions WHERE id=:d"),
            {"d": trade_decision_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def bind_quote(
        self,
        session: AsyncSession,
        *,
        trade_decision_id: int,
        token_id: str,
        checkpoint_id: int,
        checkpoint_received_at: datetime,
        best_bid: Any,
        best_ask: Any,
        price_convention: str,
        as_of: datetime,
        received_at: datetime,
        staleness_policy_ref: str,
        stale_at: datetime,
    ) -> None:
        await session.execute(
            text(
                "INSERT INTO trading.pm_quote_bindings "
                "(token_id, checkpoint_id, checkpoint_received_at, trade_decision_id, "
                " best_bid, best_ask, price_convention, as_of, received_at, "
                " staleness_policy_ref, stale_at) "
                "VALUES (:t, :c, :cr, :d, :bid, :ask, :pc, :asof, :recv, :spr, :stale) "
                "ON CONFLICT (trade_decision_id, token_id) WHERE trade_decision_id IS NOT NULL "
                "DO NOTHING"
            ),
            {
                "t": token_id, "c": checkpoint_id, "cr": checkpoint_received_at, "d": trade_decision_id,
                "bid": best_bid, "ask": best_ask, "pc": price_convention,
                "asof": as_of, "recv": received_at, "spr": staleness_policy_ref, "stale": stale_at,
            },
        )

    async def quote_bindings_for_decision(
        self, session: AsyncSession, trade_decision_id: int
    ) -> list[dict[str, Any]]:
        result = await session.execute(
            text(
                "SELECT * FROM trading.pm_quote_bindings WHERE trade_decision_id=:d "
                "ORDER BY token_id"
            ),
            {"d": trade_decision_id},
        )
        return _rows(result)

    async def mark_quote_bound(
        self, session: AsyncSession, trade_decision_id: int, *, quote_bound_at: datetime
    ) -> bool:
        result = await session.execute(
            text(
                "UPDATE trading.trade_decisions SET status='QUOTE_BOUND', quote_bound_at=:t "
                "WHERE id=:d AND status='CREATED'"
            ),
            {"d": trade_decision_id, "t": quote_bound_at},
        )
        return result.rowcount == 1

    async def advance_status(
        self, session: AsyncSession, trade_decision_id: int, *, to_status: str
    ) -> bool:
        """CREATED→QUOTE_BOUND→G7A→G7B（quote_bound_at 已设时）。"""
        result = await session.execute(
            text(
                "UPDATE trading.trade_decisions SET status=:to "
                "WHERE id=:d AND status IN ('CREATED','QUOTE_BOUND','G7A')"
            ),
            {"d": trade_decision_id, "to": to_status},
        )
        return result.rowcount == 1

    async def terminal_decision(
        self,
        session: AsyncSession,
        trade_decision_id: int,
        *,
        disposition: str,
        decided_at: datetime,
        output_hash: str,
        reason_code: str | None,
        selected_action_type: str | None,
    ) -> bool:
        result = await session.execute(
            text(
                "UPDATE trading.trade_decisions SET status=:disp, decided_at=:t, "
                "output_hash=:oh, reason_code=:rc, selected_action_type=:sat "
                "WHERE id=:d AND status='G7B'"
            ),
            {
                "d": trade_decision_id, "disp": disposition, "t": decided_at,
                "oh": output_hash, "rc": reason_code, "sat": selected_action_type,
            },
        )
        return result.rowcount == 1

    # ---------------- market-relative / discrepancy ----------------

    async def insert_market_relative_decision(
        self,
        session: AsyncSession,
        *,
        trade_decision_id: int,
        decision_mode: str,
        w_blind: Any,
        q_blind: dict,
        q_decision: dict,
        u_decision: list,
        u_blind_hash: str,
        u_decision_hash: str,
        token_gaps: dict,
        reference_identifiability: dict,
        input_manifest_hash: str,
        output_manifest_hash: str,
    ) -> int:
        result = await session.execute(
            text(
                "INSERT INTO trading.market_relative_decisions "
                "(trade_decision_id, decision_mode, w_blind, q_blind, q_decision, u_decision, "
                " u_blind_hash, u_decision_hash, token_gaps, reference_identifiability, "
                " input_manifest_hash, output_manifest_hash) "
                "VALUES (:d, :m, :w, :qb, :qd, :ud, :ubh, :udh, :tg, :ri, :ih, :oh) RETURNING id"
            ).bindparams(
                bindparam("qb", type_=JSONB()), bindparam("qd", type_=JSONB()),
                bindparam("ud", type_=JSONB()),
                bindparam("tg", type_=JSONB()), bindparam("ri", type_=JSONB()),
            ),
            {
                "d": trade_decision_id, "m": decision_mode, "w": w_blind,
                "qb": q_blind, "qd": q_decision, "ud": u_decision,
                "ubh": u_blind_hash, "udh": u_decision_hash,
                "tg": token_gaps, "ri": reference_identifiability, "ih": input_manifest_hash,
                "oh": output_manifest_hash,
            },
        )
        return result.scalar_one()

    async def get_market_relative(
        self, session: AsyncSession, trade_decision_id: int
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT * FROM trading.market_relative_decisions WHERE trade_decision_id=:d"
            ),
            {"d": trade_decision_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def get_submission_qu(
        self, session: AsyncSession, forecast_submission_id: int
    ) -> dict[str, Any] | None:
        """返回 committed submission 的 Q/U（decision belief 的权威来源）。"""
        result = await session.execute(
            text(
                "SELECT id, q AS Q, u AS U FROM trading.forecast_submissions WHERE id=:s"
            ),
            {"s": forecast_submission_id},
        )
        rows = _rows(result)
        if not rows:
            return None
        row = rows[0]
        # asyncpg 对未引用别名折叠大小写；规范化为 Q/U。
        if "Q" not in row and "q" in row:
            row["Q"] = row.pop("q")
        if "U" not in row and "u" in row:
            row["U"] = row.pop("u")
        return row

    async def get_spec_payout_hc(
        self, session: AsyncSession, contract_spec_id: int
    ) -> dict[str, Any] | None:
        """返回 spec 的 payout IR（按 token）与 h_c（component membership）。"""
        result = await session.execute(
            text(
                "SELECT cs.id AS contract_spec_id, cs.kc_resolution_states, "
                "       pf.pm_token_id, pf.function_ir "
                "FROM trading.contract_specs cs "
                "JOIN trading.payout_functions pf ON pf.contract_spec_id=cs.id "
                "WHERE cs.id=:s ORDER BY pf.outcome_index"
            ),
            {"s": contract_spec_id},
        )
        rows = _rows(result)
        if not rows:
            return None
        hc = await session.execute(
            text(
                "SELECT m.h_c FROM trading.forecast_component_contract_specs m "
                "WHERE m.contract_spec_id=:s LIMIT 1"
            ),
            {"s": contract_spec_id},
        )
        hc_row = hc.first()
        return {
            "contract_spec_id": contract_spec_id,
            "resolution_states": rows[0]["kc_resolution_states"],
            "payouts": {row["pm_token_id"]: row["function_ir"] for row in rows},
            "h_c": hc_row[0] if hc_row else {},
        }

    async def insert_discrepancy_review(
        self,
        session: AsyncSession,
        *,
        trade_decision_id: int,
        review_key: str,
        kind: str,
        result: str,
        reason_code: str | None,
        findings: dict,
    ) -> int:
        result = await session.execute(
            text(
                "INSERT INTO trading.discrepancy_reviews "
                "(trade_decision_id, review_key, kind, result, reason_code, findings) "
                "VALUES (:d, :rk, :k, :res, :rc, :f) RETURNING id"
            ).bindparams(bindparam("f", type_=JSONB())),
            {"d": trade_decision_id, "rk": review_key, "k": kind, "res": result, "rc": reason_code, "f": findings},
        )
        return result.scalar_one()

    # ---------------- G7A candidates ----------------

    async def insert_action_candidate(
        self,
        session: AsyncSession,
        *,
        trade_decision_id: int,
        contract_spec_id: int,
        token_id: int,
        action_type: str,
        fill_quantity: Any,
        vwap: Any,
        executable_depth: dict,
        cost_components: dict,
        cashflow_reconciliation_residual: Any,
        gross_edge: Any,
        break_even_payout_probability: Any,
        net_edge: Any,
        robust_ev: Any,
        point_ev: Any,
        roi: Any,
        expected_log_growth: Any,
        worst_loss: Any,
        capital_days: Any,
        edge_delay_erosion: Any,
    ) -> int:
        result = await session.execute(
            text(
                "INSERT INTO trading.action_candidates "
                "(trade_decision_id, contract_spec_id, token_id, action_type, fill_quantity, vwap, "
                " executable_depth, cost_components, cashflow_reconciliation_residual, "
                " gross_edge, break_even_payout_probability, net_edge, robust_ev, point_ev, roi, "
                " expected_log_growth, worst_loss, capital_days, edge_delay_erosion) "
                "VALUES (:d, :cs, :tk, :at, :fq, :vw, :ed, :cc, :res, "
                " :ge, :be, :ne, :rev, :pev, :roi, :elg, :wl, :cd, :ede) RETURNING id"
            ).bindparams(
                bindparam("ed", type_=JSONB()), bindparam("cc", type_=JSONB()),
            ),
            {
                "d": trade_decision_id, "cs": contract_spec_id, "tk": token_id, "at": action_type,
                "fq": fill_quantity, "vw": vwap, "ed": executable_depth, "cc": cost_components,
                "res": cashflow_reconciliation_residual, "ge": gross_edge, "be": break_even_payout_probability,
                "ne": net_edge, "rev": robust_ev, "pev": point_ev, "roi": roi,
                "elg": expected_log_growth, "wl": worst_loss, "cd": capital_days, "ede": edge_delay_erosion,
            },
        )
        return result.scalar_one()

    async def insert_cashflow(
        self,
        session: AsyncSession,
        *,
        action_candidate_id: int,
        world_state_id: str,
        cashflow: Any,
        signed_flag: str,
    ) -> None:
        await session.execute(
            text(
                "INSERT INTO trading.resolution_cashflows "
                "(action_candidate_id, world_state_id, cashflow, signed_flag) "
                "VALUES (:c, :w, :cf, :sf)"
            ),
            {"c": action_candidate_id, "w": world_state_id, "cf": cashflow, "sf": signed_flag},
        )

    async def candidates_for_decision(
        self, session: AsyncSession, trade_decision_id: int
    ) -> list[dict[str, Any]]:
        result = await session.execute(
            text(
                "SELECT ac.*, cs.contract_key, cs.version_no AS contract_spec_version, "
                "       cs.content_hash AS contract_spec_hash, "
                "       pt.token_id AS external_token_id "
                "FROM trading.action_candidates ac "
                "JOIN trading.contract_specs cs ON cs.id=ac.contract_spec_id "
                "JOIN trading.pm_tokens pt ON pt.id=ac.token_id "
                "WHERE ac.trade_decision_id=:d "
                "ORDER BY cs.contract_key, cs.content_hash, pt.token_id, ac.action_type"
            ),
            {"d": trade_decision_id},
        )
        return _rows(result)

    # ---------------- action set / underwriting / intent ----------------

    async def insert_action_set(
        self,
        session: AsyncSession,
        *,
        action_set_key: str,
        trade_decision_id: int,
        disposition: str,
        reason_code: str | None,
        wake_condition: str | None,
        recheck_at: datetime | None,
        action_set_hash: str,
    ) -> int:
        result = await session.execute(
            text(
                "INSERT INTO trading.action_sets "
                "(action_set_key, trade_decision_id, disposition, reason_code, wake_condition, "
                " recheck_at, action_set_hash) "
                "VALUES (:k, :d, :disp, :rc, :wc, :ra, :h) "
                "ON CONFLICT (action_set_key) DO NOTHING RETURNING id"
            ),
            {"k": action_set_key, "d": trade_decision_id, "disp": disposition,
             "rc": reason_code, "wc": wake_condition, "ra": recheck_at, "h": action_set_hash},
        )
        inserted = result.scalar_one_or_none()
        if inserted is not None:
            return inserted
        existing = await session.execute(
            text("SELECT id FROM trading.action_sets WHERE action_set_key=:k"),
            {"k": action_set_key},
        )
        return existing.scalar_one()

    async def insert_action_set_leg(
        self,
        session: AsyncSession,
        *,
        action_set_id: int,
        contract_spec_id: int,
        token_id: int,
        leg_role: str,
        quantity: Any,
        signed_quantity: Any,
        entry_vwap: Any,
    ) -> None:
        await session.execute(
            text(
                "INSERT INTO trading.action_set_legs "
                "(action_set_id, contract_spec_id, token_id, leg_role, quantity, "
                " signed_quantity, entry_vwap) "
                "VALUES (:s, :cs, :tk, :lr, :q, :sq, :vw)"
            ),
            {"s": action_set_id, "cs": contract_spec_id, "tk": token_id, "lr": leg_role,
             "q": quantity, "sq": signed_quantity, "vw": entry_vwap},
        )

    async def action_set_legs(
        self, session: AsyncSession, action_set_id: int
    ) -> list[dict[str, Any]]:
        result = await session.execute(
            text("SELECT * FROM trading.action_set_legs WHERE action_set_id=:s ORDER BY leg_role, token_id"),
            {"s": action_set_id},
        )
        return _rows(result)

    async def insert_underwriting_plan(
        self,
        session: AsyncSession,
        *,
        trade_decision_id: int,
        plan_version: int,
        entry_range: dict,
        hold_to_resolution: bool,
        thesis_hash: str,
        invalidation: dict,
        wake_condition: str | None,
        edge_close_threshold: Any,
        time_stop_at: datetime | None,
    ) -> int:
        result = await session.execute(
            text(
                "INSERT INTO trading.underwriting_plans "
                "(trade_decision_id, plan_version, entry_range, hold_to_resolution, thesis_hash, "
                " invalidation, wake_condition, edge_close_threshold, time_stop_at) "
                "VALUES (:d, :pv, :er, :hr, :th, :inv, :wc, :ect, :ts) RETURNING id"
            ).bindparams(bindparam("er", type_=JSONB()), bindparam("inv", type_=JSONB())),
            {
                "d": trade_decision_id, "pv": plan_version, "er": entry_range,
                "hr": hold_to_resolution, "th": thesis_hash, "inv": invalidation,
                "wc": wake_condition, "ect": edge_close_threshold, "ts": time_stop_at,
            },
        )
        return result.scalar_one()

    async def insert_action_intent(
        self,
        session: AsyncSession,
        *,
        intent_key: str,
        intent_hash: str,
        trade_decision_id: int,
        action_set_id: int,
        ttl_at: datetime | None,
        preflight: dict,
        status: str = "COMMITTED",
    ) -> int:
        result = await session.execute(
            text(
                "INSERT INTO trading.economic_action_intents "
                "(intent_key, intent_hash, trade_decision_id, action_set_id, ttl_at, preflight, status) "
                "VALUES (:k, :h, :d, :as, :ttl, :p, 'PLANNED') "
                "ON CONFLICT (intent_hash) DO NOTHING RETURNING id"
            ).bindparams(bindparam("p", type_=JSONB())),
            {"k": intent_key, "h": intent_hash, "d": trade_decision_id, "as": action_set_id,
             "ttl": ttl_at, "p": preflight},
        )
        inserted = result.scalar_one_or_none()
        if inserted is not None:
            if status == "COMMITTED":
                await session.execute(
                    text(
                        "UPDATE trading.economic_action_intents SET status='COMMITTED' "
                        "WHERE id=:i AND status='PLANNED'"
                    ),
                    {"i": inserted},
                )
            return inserted
        existing = await self.get_action_intent_by_hash(session, intent_hash)
        if existing is None:
            raise RuntimeError("action_intent_missing_after_insert")
        return existing["id"]

    async def get_action_intent_by_hash(
        self, session: AsyncSession, intent_hash: str
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text("SELECT * FROM trading.economic_action_intents WHERE intent_hash=:h"),
            {"h": intent_hash},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    # ---------------- frozen material ----------------

    async def decision_material(
        self, session: AsyncSession, episode_id: int
    ) -> dict[str, Any] | None:
        """episode + committed submission + lease + control/release binding（decision 输入冻结）。"""
        result = await session.execute(
            text(
                "SELECT fe.id AS episode_id, fe.episode_key, fe.status AS episode_status, "
                "       fe.objective_contract_id, fe.strategy_version_id, "
                "       fs.id AS submission_id, fs.submission_key, fs.Q, "
                "       fs.contract_schema_prior_evidence_hash, fs.algorithm_hash, "
                "       fim.manifest_hash AS forecast_input_manifest_hash, "
                "       fl.id AS lease_id, fl.valid_until, fl.evidence_hash, fl.schema_hash, "
                "       fl.spec_hash, "
                "       c.cohort_key, c.release_manifest_id, c.policy_hashes "
                "FROM trading.forecast_episodes fe "
                "JOIN trading.forecast_submissions fs ON fs.episode_id=fe.id "
                "  AND fs.status='BLIND_COMMITTED' "
                "JOIN trading.forecast_input_manifests fim "
                "  ON fim.id=fs.forecast_input_manifest_id "
                "LEFT JOIN trading.forecast_leases fl ON fl.submission_id=fs.id "
                "JOIN trading.decision_opportunities o ON o.id=fe.decision_opportunity_id "
                "JOIN trading.evaluation_cohorts c ON c.id=o.cohort_id "
                "WHERE fe.id=:e ORDER BY fs.id DESC LIMIT 1"
            ),
            {"e": episode_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def component_specs_for_episode(
        self, session: AsyncSession, episode_id: int
    ) -> list[dict[str, Any]]:
        result = await session.execute(
            text(
                "SELECT cs.id AS contract_spec_id, cs.contract_key, cs.kc_resolution_states, "
                "       cs.token_ids, cs.content_hash AS spec_hash "
                "FROM trading.forecast_episodes fe "
                "JOIN trading.forecast_component_versions cv ON cv.id=fe.component_version_id "
                "JOIN trading.forecast_component_contract_specs m "
                "  ON m.component_version_id=cv.id "
                "JOIN trading.contract_specs cs ON cs.id=m.contract_spec_id AND cs.status='pass' "
                "WHERE fe.id=:e ORDER BY cs.content_hash"
            ),
            {"e": episode_id},
        )
        return _rows(result)

    async def frozen_release(
        self, session: AsyncSession, release_manifest_id: int
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT r.id AS release_manifest_id, r.release_name, "
                "       r.total_hash AS release_hash, r.status AS release_status, "
                "       r.execution_spec_version_id, r.capital_permission_manifest_id, "
                "       es.status AS exec_spec_status, es.content_hash AS exec_spec_hash, "
                "       es.content AS exec_spec_content, es.created_at AS exec_spec_frozen_at, "
                "       cp.status AS capital_status, cp.mode, cp.authorized_capital, cp.kill_switch, "
                "       cp.evaluation_capital, cp.capability, cp.limits, cp.content_hash AS capital_hash "
                "FROM trading.release_manifests r "
                "JOIN trading.execution_spec_versions es ON es.id=r.execution_spec_version_id "
                "JOIN trading.capital_permission_manifests cp ON cp.id=r.capital_permission_manifest_id "
                "WHERE r.id=:r"
            ),
            {"r": release_manifest_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def release_by_episode(
        self, session: AsyncSession, episode_id: int
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT c.release_manifest_id FROM trading.forecast_episodes fe "
                "JOIN trading.decision_opportunities o ON o.id=fe.decision_opportunity_id "
                "JOIN trading.evaluation_cohorts c ON c.id=o.cohort_id "
                "WHERE fe.id=:e"
            ),
            {"e": episode_id},
        )
        row = result.first()
        if row is None:
            return None
        return await self.frozen_release(session, row[0])

    # ---------------- authoritative decision inputs ----------------

    async def decision_context(
        self, session: AsyncSession, trade_decision_id: int, *, for_update: bool = False
    ) -> dict[str, Any] | None:
        """Load the exact committed cognition, lease and frozen release for a decision."""
        suffix = " FOR UPDATE OF td" if for_update else ""
        result = await session.execute(
            text(
                "SELECT td.*, fe.episode_key, fe.status AS episode_status, "
                "       fe.component_version_id, cv.component_id, "
                "       fs.submission_key, fs.status AS submission_status, "
                "       fs.q AS committed_q, fs.u AS committed_u, "
                "       fl.valid_until AS lease_valid_until, "
                "       r.status AS release_status, es.status AS exec_spec_status, "
                "       es.content AS exec_spec_content, es.content_hash AS exec_spec_hash, "
                "       es.created_at AS exec_spec_frozen_at, cp.status AS capital_status, "
                "       cp.mode AS capital_mode, cp.evaluation_capital, cp.authorized_capital, "
                "       cp.kill_switch, cp.capability, cp.limits, cp.content_hash AS capital_hash, "
                "       sv.content AS strategy_content, sv.content_hash AS strategy_hash "
                "FROM trading.trade_decisions td "
                "JOIN trading.forecast_episodes fe ON fe.id=td.episode_id "
                "JOIN trading.forecast_component_versions cv ON cv.id=fe.component_version_id "
                "JOIN trading.forecast_submissions fs ON fs.id=td.forecast_submission_id "
                "JOIN trading.forecast_leases fl ON fl.id=td.forecast_lease_id "
                "JOIN trading.release_manifests r ON r.id=td.release_manifest_id "
                " AND r.execution_spec_version_id=td.execution_spec_version_id "
                " AND r.capital_permission_manifest_id=td.capital_permission_manifest_id "
                "JOIN trading.execution_spec_versions es ON es.id=td.execution_spec_version_id "
                "JOIN trading.strategy_versions sv ON sv.id=td.strategy_version_id "
                "JOIN trading.capital_permission_manifests cp "
                " ON cp.id=td.capital_permission_manifest_id "
                "WHERE td.id=:d" + suffix
            ),
            {"d": trade_decision_id},
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def required_tokens_for_decision(
        self, session: AsyncSession, trade_decision_id: int
    ) -> list[dict[str, Any]]:
        """Exact episode token set plus payout/world/market mapping."""
        result = await session.execute(
            text(
                "SELECT ecs.contract_spec_id, pf.pm_token_id AS token_id, "
                "       pt.token_id AS external_token_id, pt.outcome_index, pt.market_id, "
                "       pm.active AS market_active, pm.closed AS market_closed, "
                "       pm.accepting_orders, pm.enable_order_book, pm.end_date, "
                "       cv.component_id, m.h_c, pf.function_ir AS payout_ir "
                "FROM trading.trade_decisions td "
                "JOIN trading.forecast_episodes fe ON fe.id=td.episode_id "
                "JOIN trading.forecast_component_versions cv ON cv.id=fe.component_version_id "
                "JOIN trading.episode_contract_specs ecs ON ecs.episode_id=td.episode_id "
                "JOIN trading.forecast_component_contract_specs m "
                " ON m.component_version_id=fe.component_version_id "
                " AND m.contract_spec_id=ecs.contract_spec_id "
                "JOIN trading.payout_functions pf ON pf.contract_spec_id=ecs.contract_spec_id "
                "JOIN trading.pm_tokens pt ON pt.id=pf.pm_token_id "
                "JOIN trading.pm_markets pm ON pm.id=pt.market_id "
                "WHERE td.id=:d "
                "ORDER BY ecs.contract_spec_id, pt.outcome_index, pt.token_id"
            ),
            {"d": trade_decision_id},
        )
        return _rows(result)

    async def episode_markets(
        self, session: AsyncSession, episode_id: int
    ) -> list[dict[str, Any]]:
        result = await session.execute(
            text(
                "SELECT DISTINCT pm.id AS market_id, pm.active, pm.closed, pm.accepting_orders, "
                "       pm.enable_order_book, pm.end_date "
                "FROM trading.episode_contract_specs ecs "
                "JOIN trading.payout_functions pf ON pf.contract_spec_id=ecs.contract_spec_id "
                "JOIN trading.pm_tokens pt ON pt.id=pf.pm_token_id "
                "JOIN trading.pm_markets pm ON pm.id=pt.market_id "
                "WHERE ecs.episode_id=:e ORDER BY pm.id"
            ),
            {"e": episode_id},
        )
        return _rows(result)

    async def checkpoint_material(
        self,
        session: AsyncSession,
        *,
        external_token_id: str,
        checkpoint_id: int,
        checkpoint_received_at: datetime,
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT id AS checkpoint_id, token_id AS external_token_id, received_at, "
                "       best_bid, best_ask, tick_size, min_order_size, completeness, validity "
                "FROM trading.pm_book_checkpoints "
                "WHERE id=:c AND received_at=:r AND token_id=:t"
            ),
            {"c": checkpoint_id, "r": checkpoint_received_at, "t": external_token_id},
        )
        rows = _rows(result)
        if not rows:
            return None
        levels = await session.execute(
            text(
                "SELECT side, price, size, ordinal FROM trading.pm_book_levels "
                "WHERE checkpoint_id=:c AND received_at=:r "
                "ORDER BY side, ordinal, price"
            ),
            {"c": checkpoint_id, "r": checkpoint_received_at},
        )
        row = rows[0]
        row["levels"] = _rows(levels)
        return row

    async def bound_market_material(
        self, session: AsyncSession, trade_decision_id: int
    ) -> list[dict[str, Any]]:
        result = await session.execute(
            text(
                "SELECT q.token_id AS external_token_id, q.checkpoint_id, "
                "       q.checkpoint_received_at, q.best_bid, q.best_ask, q.as_of, "
                "       q.received_at, q.stale_at, pt.id AS token_id, pt.market_id, "
                "       pf.contract_spec_id, pf.function_ir AS payout_ir, m.h_c, "
                "       cv.component_id "
                "FROM trading.pm_quote_bindings q "
                "JOIN trading.trade_decisions td ON td.id=q.trade_decision_id "
                "JOIN trading.forecast_episodes fe ON fe.id=td.episode_id "
                "JOIN trading.forecast_component_versions cv ON cv.id=fe.component_version_id "
                "JOIN trading.pm_tokens pt ON pt.token_id=q.token_id "
                "JOIN trading.payout_functions pf ON pf.pm_token_id=pt.id "
                "JOIN trading.episode_contract_specs ecs ON ecs.episode_id=td.episode_id "
                " AND ecs.contract_spec_id=pf.contract_spec_id "
                "JOIN trading.forecast_component_contract_specs m "
                " ON m.component_version_id=fe.component_version_id "
                " AND m.contract_spec_id=pf.contract_spec_id "
                "WHERE q.trade_decision_id=:d "
                "ORDER BY pf.contract_spec_id, pt.outcome_index, q.token_id"
            ),
            {"d": trade_decision_id},
        )
        rows = _rows(result)
        for row in rows:
            levels = await session.execute(
                text(
                    "SELECT side, price, size, ordinal FROM trading.pm_book_levels "
                    "WHERE checkpoint_id=:c AND received_at=:r "
                    "ORDER BY side, ordinal, price"
                ),
                {"c": row["checkpoint_id"], "r": row["checkpoint_received_at"]},
            )
            row["levels"] = _rows(levels)
        return rows

    async def allocated_operating_cost(
        self, session: AsyncSession, trade_decision_id: int
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT count(*) AS evidence_count, COALESCE(sum(o.amount),0) AS amount, "
                "       jsonb_agg(o.allocation_policy ORDER BY o.id) AS policies "
                "FROM trading.trade_decisions td "
                "JOIN trading.operating_cost_entries o ON "
                " (o.trade_decision_id=td.id OR "
                "  (o.trade_decision_id IS NULL AND o.episode_id=td.episode_id) OR "
                "  (o.trade_decision_id IS NULL AND o.episode_id IS NULL "
                "   AND o.release_manifest_id=td.release_manifest_id)) "
                "WHERE td.id=:d"
            ),
            {"d": trade_decision_id},
        )
        row = result.mappings().one()
        return dict(row) if row["evidence_count"] else None

    async def cognition_review_passed(
        self,
        session: AsyncSession,
        *,
        episode_id: int,
        forecast_submission_id: int,
    ) -> bool:
        result = await session.execute(
            text(
                "SELECT EXISTS ("
                " SELECT 1 FROM trading.forecast_episodes fe "
                " JOIN trading.forecast_submissions fs "
                "   ON fs.id=:s AND fs.episode_id=fe.id "
                " WHERE fe.id=:e "
                "   AND fe.status='BLIND_COMMITTED' "
                "   AND fe.cognition_status='COMMITTED' "
                "   AND fs.status='BLIND_COMMITTED' AND fs.committed_at IS NOT NULL "
                "   AND EXISTS (SELECT 1 FROM trading.gate_decisions gd "
                "     WHERE gd.gate='G6' AND gd.target_kind='episode' "
                "       AND gd.target_id=fe.id AND gd.result='PASS') "
                "   AND EXISTS (SELECT 1 FROM trading.coherence_checks cc "
                "     WHERE cc.submission_id=fs.id AND cc.check_name='q_nonneg_total' "
                "       AND cc.severity='hard' AND cc.passed) "
                "   AND EXISTS (SELECT 1 FROM trading.coherence_checks cc "
                "     WHERE cc.submission_id=fs.id AND cc.check_name='u_contains_q' "
                "       AND cc.severity='hard' AND cc.passed) "
                "   AND NOT EXISTS (SELECT 1 FROM trading.episode_contract_specs ecs "
                "     WHERE ecs.episode_id=fe.id AND NOT EXISTS ("
                "       SELECT 1 FROM trading.coherence_checks cc "
                "       WHERE cc.submission_id=fs.id "
                "         AND cc.check_name='projection_complete:' || ecs.contract_spec_id::text "
                "         AND cc.severity='hard' AND cc.passed)) "
                "   AND NOT EXISTS (SELECT 1 FROM trading.coherence_checks cc "
                "     WHERE cc.submission_id=fs.id "
                "       AND cc.severity='hard' AND NOT cc.passed) "
                ")"
            ),
            {"e": episode_id, "s": forecast_submission_id},
        )
        return bool(result.scalar_one())

    async def review_passed(self, session: AsyncSession, trade_decision_id: int) -> bool:
        result = await session.execute(
            text(
                "SELECT td.episode_id, td.forecast_submission_id, EXISTS ("
                " SELECT 1 FROM trading.discrepancy_reviews dr "
                " WHERE dr.trade_decision_id=td.id "
                "   AND dr.kind='book_integrity' AND dr.result='PASS'"
                ") AS book_reviewed FROM trading.trade_decisions td WHERE td.id=:d"
            ),
            {"d": trade_decision_id},
        )
        row = result.mappings().first()
        if row is None or not row["book_reviewed"]:
            return False
        return await self.cognition_review_passed(
            session,
            episode_id=row["episode_id"],
            forecast_submission_id=row["forecast_submission_id"],
        )

    async def has_position_for_decision(
        self, session: AsyncSession, trade_decision_id: int, portfolio_namespace: str
    ) -> bool:
        result = await session.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM trading.positions p "
                " JOIN trading.episode_contract_specs ecs ON ecs.contract_spec_id=p.contract_spec_id "
                " JOIN trading.trade_decisions td ON td.episode_id=ecs.episode_id "
                " WHERE td.id=:d AND p.portfolio_namespace=:ns AND p.quantity>0)"
            ),
            {"d": trade_decision_id, "ns": portfolio_namespace},
        )
        return bool(result.scalar_one())

    async def has_valid_underwriting(
        self, session: AsyncSession, trade_decision_id: int, at: datetime
    ) -> bool:
        result = await session.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM trading.underwriting_plans up "
                " JOIN trading.trade_decisions source ON source.id=up.trade_decision_id "
                " JOIN trading.trade_decisions current ON current.episode_id=source.episode_id "
                " WHERE current.id=:d AND up.hold_to_resolution "
                " AND (up.time_stop_at IS NULL OR up.time_stop_at>:at))"
            ),
            {"d": trade_decision_id, "at": at},
        )
        return bool(result.scalar_one())
