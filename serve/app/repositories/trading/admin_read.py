"""V2 Admin Read API repository（WP-07A Checkpoint A/B）。

- 每个 endpoint 使用独立静态 SQL；禁止客户端传表名/列名/SQL 片段。
- 列表统一 keyset ``(sort_key, id)``；绝不使用 OFFSET、深页 COUNT(*) 或任意字段排序。
- 列表/ detail 只投影摘要列；绝不默认返回 raw prompt/response/signed body/book levels/大 JSON。
- BIGINT id 以 ``::text`` 返回（decimal string），NUMERIC 以 ``::text`` 返回（decimal string），
  禁止 JS number/float。
- Repository 不 commit、不读 env、不调用 Redis/network。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _rows(result) -> list[dict[str, Any]]:
    keys = list(result.keys())
    return [dict(zip(keys, row)) for row in result.fetchall()]


def _one(result) -> dict[str, Any] | None:
    rows = _rows(result)
    return rows[0] if rows else None


_PROJECTION_SQL = {
    "ops_health_current": (
        "SELECT id::text AS id, metric_name, metric_value::text AS metric_value, status, "
        "as_of::text AS as_of, source_high_watermark::text AS source_high_watermark, "
        "projection_version, projection_hash, created_at::text AS created_at "
        "FROM trading.ops_health_current ORDER BY id"
    ),
    "pipeline_funnel_hourly": (
        "SELECT id::text AS id, hour_start::text AS hour_start, stage, "
        "event_count::text AS event_count, as_of::text AS as_of, "
        "source_high_watermark::text AS source_high_watermark, projection_version, "
        "projection_hash, created_at::text AS created_at "
        "FROM trading.pipeline_funnel_hourly ORDER BY id"
    ),
    "account_risk_current": (
        "SELECT id::text AS id, portfolio_namespace, market_id::text AS market_id, "
        "component_id::text AS component_id, exposure::text AS exposure, "
        "net_risk_capital::text AS net_risk_capital, cvar::text AS cvar, "
        "capital_days::text AS capital_days, as_of::text AS as_of, "
        "source_high_watermark::text AS source_high_watermark, projection_version, "
        "projection_hash, created_at::text AS created_at "
        "FROM trading.account_risk_current ORDER BY id"
    ),
    "provider_cost_daily": (
        "SELECT id::text AS id, cost_date::text AS cost_date, provider, cost_kind, "
        "amount::text AS amount, as_of::text AS as_of, "
        "source_high_watermark::text AS source_high_watermark, projection_version, "
        "projection_hash, created_at::text AS created_at "
        "FROM trading.provider_cost_daily ORDER BY id"
    ),
    "latest_chain_summary": (
        "SELECT id::text AS id, chain_key, chain_value::text AS chain_value, "
        "period_end::text AS period_end, as_of::text AS as_of, "
        "source_high_watermark::text AS source_high_watermark, projection_version, "
        "projection_hash, created_at::text AS created_at "
        "FROM trading.latest_chain_summary ORDER BY id"
    ),
}


class AdminReadRepository:
    """V2 Admin read plane；只拥有 SQL，不做业务判断。"""

    # =====================================================================
    # Dashboard —— 只读 WP-04 五张 projection（不扫事实大表）
    # =====================================================================

    async def dashboard_projection(self, session: AsyncSession, table: str) -> list[dict]:
        sql = _PROJECTION_SQL.get(table)
        if sql is None:
            raise ValueError(f"dashboard_projection table not allowed: {table!r}")
        result = await session.execute(text(sql))
        return _rows(result)

    # =====================================================================
    # keyset 辅助
    # =====================================================================

    async def _keyset_page(
        self, session, *, table: str, columns: str, sort_col: str, direction: str,
        cursor_st: Any | None, cursor_id: str | None, limit: int, as_of: Any,
        extra_where: str = "", params: dict | None = None,
    ) -> tuple[list[dict], bool]:
        op = ">" if direction == "asc" else "<"
        order = "ASC" if direction == "asc" else "DESC"
        base_params: dict = {"st": cursor_st, "as_of": as_of,
                            "cid": int(cursor_id) if cursor_id is not None else None}
        if params:
            base_params.update(params)
        # 首屏与后续页始终应用 allowlisted filters 和 frozen as_of。cursor 仅追加
        # tuple 边界；绝不因首屏没有 cursor 而丢弃过滤条件。
        predicates = [f"{sort_col} <= :as_of"]
        if cursor_st is not None and cursor_id is not None:
            predicates.append(f"( {sort_col}, id ) {op} ( :st, :cid )")
        if extra_where:
            predicates.append(extra_where)
        where = " AND ".join(f"({item})" for item in predicates)
        sql = f"SELECT {columns} FROM trading.{table} "
        sql += f"WHERE {where} "
        sql += f"ORDER BY {sort_col} {order}, id {order} LIMIT :lim"
        result = await session.execute(text(sql), {**base_params, "lim": limit + 1})
        rows = _rows(result)
        return rows[:limit], len(rows) > limit

    # =====================================================================
    # Markets
    # =====================================================================

    _MARKET_COLS = (
        "gamma_market_id, gamma_event_id, condition_id, question, slug, ticker, "
        "active, closed, accepting_orders, neg_risk, "
        "volume::text AS volume, liquidity::text AS liquidity, "
        "start_date::text AS start_date, end_date::text AS end_date, "
        "closed_at::text AS closed_at, created_at::text AS created_at, id::text AS id"
    )

    async def list_markets(self, session, *, cursor_st, cursor_id, direction, limit, as_of,
                           neg_risk=None, closed=None) -> tuple[list[dict], bool]:
        extra, params = [], {}
        if neg_risk is not None:
            extra.append("neg_risk = :neg_risk")
            params["neg_risk"] = neg_risk
        if closed is not None:
            extra.append("closed = :closed")
            params["closed"] = closed
        return await self._keyset_page(
            session, table="pm_markets", columns=self._MARKET_COLS,
            sort_col="created_at", direction=direction,
            cursor_st=cursor_st, cursor_id=cursor_id, limit=limit, as_of=as_of,
            extra_where=" AND ".join(extra), params=params,
        )

    _TAG_COLS = (
        "gamma_tag_id, slug, label, seen_in_catalog, seen_in_event, disposition, "
        "(SELECT count(*)::text FROM trading.pm_event_tags e "
        "  WHERE e.gamma_tag_id = pm_tags.gamma_tag_id) AS event_count, "
        "observed_at::text AS observed_at, created_at::text AS created_at, "
        "id::text AS id"
    )

    async def list_tags(self, session, *, cursor_st, cursor_id, direction, limit, as_of,
                        slug=None, seen_in_catalog=None, disposition=None
                        ) -> tuple[list[dict], bool]:
        extra, params = [], {}
        if slug:
            extra.append("slug = :slug")
            params["slug"] = slug
        if seen_in_catalog is not None:
            extra.append("seen_in_catalog = :seen_in_catalog")
            params["seen_in_catalog"] = seen_in_catalog
        if disposition:
            if disposition == "unset":
                extra.append("disposition IS NULL")
            else:
                extra.append("disposition = :disposition")
                params["disposition"] = disposition
        return await self._keyset_page(
            session, table="pm_tags", columns=self._TAG_COLS,
            sort_col="created_at", direction=direction,
            cursor_st=cursor_st, cursor_id=cursor_id, limit=limit, as_of=as_of,
            extra_where=" AND ".join(extra), params=params,
        )

    async def get_market(self, session, market_id: int) -> dict | None:
        result = await session.execute(
            text(
                "SELECT gamma_market_id, gamma_event_id, condition_id, question, slug, ticker, "
                " active, closed, accepting_orders, neg_risk, "
                " volume::text AS volume, liquidity::text AS liquidity, "
                " start_date::text AS start_date, end_date::text AS end_date, "
                " closed_at::text AS closed_at, content_hash, raw_artifact_ref, "
                " created_at::text AS created_at, id::text AS id "
                "FROM trading.pm_markets WHERE id = :mid"
            ),
            {"mid": market_id},
        )
        return _one(result)

    async def market_chain(self, session, market_id: int) -> dict:
        market = await self.get_market(session, market_id)
        if market is None:
            return {}
        chain: dict = {"market": market}
        snap = await session.execute(
            text(
                "SELECT cs.question, cs.rules, cs.clarification, cs.resolution_source, "
                " cs.cutoff_at::text AS cutoff_at, cs.content_hash, "
                " cs.market_version_id::text AS market_version_id, "
                " cs.yes_token_version_id::text AS yes_token_version_id, "
                " cs.no_token_version_id::text AS no_token_version_id, "
                " cs.artifact_object_id::text AS artifact_object_id "
                "FROM trading.contract_snapshots cs "
                "WHERE cs.market_version_id IN "
                " (SELECT id FROM trading.pm_market_versions WHERE market_id = :mid) "
                "ORDER BY cs.id DESC LIMIT 1"
            ),
            {"mid": market_id},
        )
        chain["snapshot"] = _one(snap)
        specs = await session.execute(
            text(
                "SELECT cspec.id::text AS id, cspec.contract_key, cspec.version_no, "
                " cspec.token_count, cspec.state_count, cspec.status, "
                " cspec.content_hash, pf.id::text AS payout_function_id, "
                " pf.pm_token_id::text AS pm_token_id, "
                " pf.token_version_id::text AS token_version_id, "
                " pf.outcome_index, pf.algorithm_hash, "
                " pf.content_hash AS payout_content_hash "
                "FROM trading.contract_specs cspec "
                "LEFT JOIN trading.payout_functions pf ON pf.contract_spec_id = cspec.id "
                "WHERE cspec.snapshot_id IN (SELECT cs.id FROM trading.contract_snapshots cs "
                " WHERE cs.market_version_id IN (SELECT id FROM trading.pm_market_versions "
                "  WHERE market_id = :mid)) "
                "ORDER BY cspec.id LIMIT 20"
            ),
            {"mid": market_id},
        )
        chain["specs"] = _rows(specs)
        current = await session.execute(
            text(
                "SELECT id::text AS id, market_id::text AS market_id, condition_id, "
                " gamma_market_id, tokens_ok, mapping_state, eligible, current_version_no, "
                " observed_at::text AS observed_at, content_hash, "
                " updated_at::text AS updated_at, created_at::text AS created_at "
                "FROM trading.pm_market_current WHERE market_id = :mid"
            ),
            {"mid": market_id},
        )
        chain["current"] = _one(current)
        cohort = await session.execute(
            text(
                "SELECT um.id::text AS id, um.cohort_id::text AS cohort_id, "
                " ec.cohort_key, ec.status, ec.opened_at::text AS opened_at "
                "FROM trading.universe_memberships um "
                "JOIN trading.evaluation_cohorts ec ON ec.id = um.cohort_id "
                "WHERE um.market_id = :mid LIMIT 10"
            ),
            {"mid": market_id},
        )
        chain["cohort"] = _rows(cohort)
        return chain

    # =====================================================================
    # Components
    # =====================================================================

    _COMPONENT_COLS = (
        "id::text AS id, component_key, cost_budget::text AS cost_budget, description, "
        "created_at::text AS created_at"
    )

    async def list_components(self, session, *, cursor_st, cursor_id, direction, limit,
                              as_of) -> tuple[list[dict], bool]:
        return await self._keyset_page(
            session, table="forecast_components", columns=self._COMPONENT_COLS,
            sort_col="created_at", direction=direction, cursor_st=cursor_st,
            cursor_id=cursor_id, limit=limit, as_of=as_of,
        )

    async def get_component(self, session, component_id: int) -> dict | None:
        result = await session.execute(
            text(
                "SELECT id::text AS id, component_key, cost_budget::text AS cost_budget, description, "
                " created_at::text AS created_at "
                "FROM trading.forecast_components WHERE id = :cid"
            ),
            {"cid": component_id},
        )
        return _one(result)

    async def component_chain(self, session, component_id: int) -> dict:
        component = await self.get_component(session, component_id)
        if component is None:
            return {}
        chain: dict = {"component": component}
        versions = await session.execute(
            text(
                "SELECT id::text AS id, version_no, cost_budget::text AS cost_budget, "
                " status, content_hash, "
                " effective_from::text AS effective_from, effective_until::text AS effective_until, "
                " created_at::text AS created_at "
                "FROM trading.forecast_component_versions "
                "WHERE component_id = :cid ORDER BY version_no DESC LIMIT 50"
            ),
            {"cid": component_id},
        )
        chain["versions"] = _rows(versions)
        members = await session.execute(
            text(
                "SELECT fccs.id::text AS id, fccs.contract_spec_id::text AS contract_spec_id, "
                " fccs.component_version_id::text AS component_version_id, "
                " fccs.totality_test_hash "
                "FROM trading.forecast_component_contract_specs fccs "
                "JOIN trading.forecast_component_versions fcv "
                " ON fcv.id = fccs.component_version_id AND fcv.component_id = :cid "
                "ORDER BY fccs.component_version_id DESC LIMIT 50"
            ),
            {"cid": component_id},
        )
        chain["member_contracts"] = _rows(members)
        return chain

    # =====================================================================
    # Episodes
    # =====================================================================

    _EPISODE_COLS = (
        "fe.id::text AS id, fe.episode_key, fe.decision_opportunity_id::text AS "
        "decision_opportunity_id, fe.component_version_id::text AS component_version_id, "
        "fe.strategy_version_id::text AS strategy_version_id, fe.trigger, "
        "fe.cutoff_at::text AS cutoff_at, fe.horizon, fe.status, fe.cognition_status, "
        "fe.prior_frozen_at::text AS prior_frozen_at, "
        "fe.forecast_committed_at::text AS forecast_committed_at, "
        "fe.created_at::text AS created_at"
    )

    async def list_episodes(self, session, *, cursor_st, cursor_id, direction, limit, as_of,
                            status=None) -> tuple[list[dict], bool]:
        extra, params = [], {}
        if status is not None:
            extra.append("fe.status = :status")
            params["status"] = status
        return await self._keyset_page(
            session, table="forecast_episodes fe", columns=self._EPISODE_COLS,
            sort_col="fe.created_at", direction=direction,
            cursor_st=cursor_st, cursor_id=cursor_id, limit=limit, as_of=as_of,
            extra_where=" AND ".join(extra), params=params,
        )

    async def get_episode(self, session, episode_id: int) -> dict | None:
        result = await session.execute(
            text(
                "SELECT id::text AS id, episode_key, "
                " decision_opportunity_id::text AS decision_opportunity_id, "
                " component_version_id::text AS component_version_id, "
                " strategy_version_id::text AS strategy_version_id, "
                " objective_contract_id::text AS objective_contract_id, "
                " trigger, cutoff_at::text AS cutoff_at, horizon, status, "
                " drop_reason, cognition_status, prior_frozen_at::text AS prior_frozen_at, "
                " evidence_bundle_at::text AS evidence_bundle_at, "
                " forecast_committed_at::text AS forecast_committed_at, "
                " created_at::text AS created_at "
                "FROM trading.forecast_episodes WHERE id = :eid"
            ),
            {"eid": episode_id},
        )
        return _one(result)

    async def episode_chain(self, session, episode_id: int) -> dict:
        episode = await self.get_episode(session, episode_id)
        if episode is None:
            return {}
        chain: dict = {"episode": episode}
        prior = await session.execute(
            text(
                "SELECT id::text AS id, version_no, reference_class, hazard_ref, "
                " market_blind_declaration, content_hash, status, "
                " created_at::text AS created_at "
                "FROM trading.priors WHERE episode_id = :eid ORDER BY id DESC LIMIT 5"
            ),
            {"eid": episode_id},
        )
        chain["priors"] = _rows(prior)
        evidence = await session.execute(
            text(
                "SELECT id::text AS id, bundle_key, bundle_hash, status, "
                " information_cutoff_at::text AS information_cutoff_at, "
                " created_at::text AS created_at "
                "FROM trading.evidence_bundles WHERE episode_id = :eid "
                "ORDER BY id DESC LIMIT 5"
            ),
            {"eid": episode_id},
        )
        chain["evidence_bundles"] = _rows(evidence)
        submissions = await session.execute(
            text(
                "SELECT id::text AS id, submission_key, status, "
                " contract_schema_prior_evidence_hash, algorithm_hash, "
                " committed_at::text AS committed_at, "
                " created_at::text AS created_at "
                "FROM trading.forecast_submissions WHERE episode_id = :eid "
                "ORDER BY id DESC LIMIT 5"
            ),
            {"eid": episode_id},
        )
        chain["submissions"] = _rows(submissions)
        gates = await session.execute(
            text(
                "SELECT id::text AS id, gate, result, reason_code, "
                " committed_at::text AS committed_at "
                "FROM trading.gate_decisions "
                "WHERE target_kind = 'episode' AND target_id = :eid "
                "ORDER BY id DESC LIMIT 20"
            ),
            {"eid": episode_id},
        )
        chain["gates"] = _rows(gates)
        return chain

    async def episode_timeline(self, session, *, episode_id: int, cursor_st, cursor_id,
                               direction, limit, as_of) -> tuple[list[dict], bool]:
        op = ">" if direction == "asc" else "<"
        order = "ASC" if direction == "asc" else "DESC"
        cursor_where = ""
        if cursor_st is not None and cursor_id is not None:
            cursor_where = f"AND (timeline.created_at, timeline.id) {op} (:st, :cid) "
        result = await session.execute(
            text(
                "SELECT timeline.kind, timeline.id::text AS id, "
                " timeline.created_at::text AS created_at, timeline.state "
                "FROM ("
                " SELECT 'submission' AS kind, fs.id, fs.created_at, fs.status AS state "
                " FROM trading.forecast_submissions fs WHERE fs.episode_id = :eid "
                " UNION ALL "
                " SELECT 'gate' AS kind, gd.id, gd.committed_at AS created_at, gd.gate AS state "
                " FROM trading.gate_decisions gd "
                " WHERE gd.target_kind = 'episode' AND gd.target_id = :eid "
                " UNION ALL "
                " SELECT 'info_snapshot' AS kind, inf.id, inf.created_at, inf.gate AS state "
                " FROM trading.information_snapshots inf WHERE inf.episode_id = :eid"
                ") timeline "
                "WHERE timeline.created_at <= :as_of "
                + cursor_where
                + f"ORDER BY timeline.created_at {order}, timeline.id {order} LIMIT :lim"
            ),
            {"eid": episode_id, "as_of": as_of, "st": cursor_st,
             "cid": int(cursor_id) if cursor_id is not None else None,
             "lim": limit + 1},
        )
        rows = _rows(result)
        has_more = len(rows) > limit
        return rows[:limit], has_more

    # =====================================================================
    # Decisions
    # =====================================================================

    _DECISION_COLS = (
        "td.id::text AS id, td.decision_key, td.episode_id::text AS episode_id, "
        "td.forecast_submission_id::text AS forecast_submission_id, "
        "td.strategy_version_id::text AS strategy_version_id, "
        "td.release_manifest_id::text AS release_manifest_id, "
        "td.decision_class, td.status, td.selected_action_type, "
        "td.trigger_at::text AS trigger_at, td.decided_at::text AS decided_at, "
        "td.input_hash, td.output_hash, td.reason_code, "
        "td.created_at::text AS created_at"
    )

    async def list_decisions(self, session, *, cursor_st, cursor_id, direction, limit, as_of,
                             status=None, decision_class=None) -> tuple[list[dict], bool]:
        extra, params = [], {}
        if status is not None:
            extra.append("td.status = :status")
            params["status"] = status
        if decision_class is not None:
            extra.append("td.decision_class = :decision_class")
            params["decision_class"] = decision_class
        return await self._keyset_page(
            session, table="trade_decisions td", columns=self._DECISION_COLS,
            sort_col="td.created_at", direction=direction,
            cursor_st=cursor_st, cursor_id=cursor_id, limit=limit, as_of=as_of,
            extra_where=" AND ".join(extra), params=params,
        )

    async def get_decision(self, session, decision_id: int) -> dict | None:
        result = await session.execute(
            text(
                "SELECT id::text AS id, decision_key, episode_id::text AS episode_id, "
                " forecast_submission_id::text AS forecast_submission_id, "
                " forecast_lease_id::text AS forecast_lease_id, "
                " strategy_version_id::text AS strategy_version_id, "
                " release_manifest_id::text AS release_manifest_id, "
                " capital_permission_manifest_id::text AS capital_permission_manifest_id, "
                " decision_class, status, selected_action_type, "
                " trigger_at::text AS trigger_at, quote_bound_at::text AS quote_bound_at, "
                " decided_at::text AS decided_at, input_hash, output_hash, reason_code, "
                " created_at::text AS created_at "
                "FROM trading.trade_decisions WHERE id = :did"
            ),
            {"did": decision_id},
        )
        return _one(result)

    async def decision_chain(self, session, decision_id: int) -> dict:
        decision = await self.get_decision(session, decision_id)
        if decision is None:
            return {}
        chain: dict = {"decision": decision}
        quote = await session.execute(
            text(
                "SELECT qb.id::text AS id, qb.token_id::text AS token_id, "
                " qb.best_bid::text AS best_bid, qb.best_ask::text AS best_ask, "
                " qb.stale_at::text AS stale_at, "
                " qb.checkpoint_received_at::text AS checkpoint_received_at, "
                " qb.as_of::text AS as_of "
                "FROM trading.pm_quote_bindings qb "
                "WHERE qb.trade_decision_id = :did ORDER BY qb.id DESC LIMIT 3"
            ),
            {"did": decision_id},
        )
        chain["quote_bindings"] = _rows(quote)
        plans = await session.execute(
            text(
                "SELECT id::text AS id, plan_version, hold_to_resolution, "
                " thesis_hash, wake_condition, "
                " edge_close_threshold::text AS edge_close_threshold, "
                " time_stop_at::text AS time_stop_at "
                "FROM trading.underwriting_plans WHERE trade_decision_id = :did "
                "ORDER BY plan_version DESC LIMIT 5"
            ),
            {"did": decision_id},
        )
        chain["underwriting_plans"] = _rows(plans)
        actions = await session.execute(
            text(
                "SELECT aset.id::text AS id, aset.action_set_key, aset.disposition, "
                " aset.action_set_hash, aset.reason_code, aset.created_at::text AS created_at "
                "FROM trading.action_sets aset WHERE aset.trade_decision_id = :did "
                "ORDER BY aset.id DESC LIMIT 10"
            ),
            {"did": decision_id},
        )
        chain["action_sets"] = _rows(actions)
        intents = await session.execute(
            text(
                "SELECT id::text AS id, intent_key, intent_hash, status, "
                " ttl_at::text AS ttl_at, created_at::text AS created_at "
                "FROM trading.economic_action_intents WHERE trade_decision_id = :did "
                "ORDER BY id DESC LIMIT 20"
            ),
            {"did": decision_id},
        )
        chain["intents"] = _rows(intents)
        return chain

    # =====================================================================
    # Execution —— intents / orders / positions / ledger / decision trace
    # =====================================================================

    async def list_intents(self, session, *, cursor_st, cursor_id, direction, limit, as_of,
                           status=None) -> tuple[list[dict], bool]:
        extra, params = [], {}
        if status is not None:
            extra.append("eai.status = :status")
            params["status"] = status
        return await self._keyset_page(
            session, table="economic_action_intents eai", columns=(
                "eai.id::text AS id, eai.intent_key, eai.intent_hash, "
                " eai.trade_decision_id::text AS trade_decision_id, "
                " eai.action_set_id::text AS action_set_id, eai.status, "
                " eai.ttl_at::text AS ttl_at, eai.created_at::text AS created_at"
            ), sort_col="eai.created_at", direction=direction,
            cursor_st=cursor_st, cursor_id=cursor_id, limit=limit, as_of=as_of,
            extra_where=" AND ".join(extra), params=params,
        )

    async def list_orders(self, session, *, cursor_st, cursor_id, direction, limit, as_of,
                          status=None) -> tuple[list[dict], bool]:
        extra, params = [], {}
        if status is not None:
            extra.append("eo.status = :status")
            params["status"] = status
        return await self._keyset_page(
            session, table="exchange_orders eo", columns=(
                "eo.id::text AS id, eo.order_key, eo.external_order_id, "
                " eo.token_id, eo.side, eo.price::text AS price, eo.size::text AS size, "
                " eo.filled_size::text AS filled_size, eo.status, "
                " eo.account_id::text AS account_id, eo.created_at::text AS created_at"
            ), sort_col="eo.created_at", direction=direction,
            cursor_st=cursor_st, cursor_id=cursor_id, limit=limit, as_of=as_of,
            extra_where=" AND ".join(extra), params=params,
        )

    async def list_positions(self, session, *, cursor_st, cursor_id, direction, limit,
                             as_of) -> tuple[list[dict], bool]:
        return await self._keyset_page(
            session, table="positions", columns=(
                "id::text AS id, portfolio_namespace, contract_spec_id::text AS contract_spec_id, "
                "token_id::text AS token_id, market_id::text AS market_id, "
                "quantity::text AS quantity, cost_basis::text AS cost_basis, "
                "account_id::text AS account_id, updated_at::text AS updated_at"
            ), sort_col="updated_at", direction=direction, cursor_st=cursor_st,
            cursor_id=cursor_id, limit=limit, as_of=as_of,
        )

    async def list_ledger(self, session, *, cursor_st, cursor_id, direction, limit, as_of,
                          kind=None) -> tuple[list[dict], bool]:
        extra, params = [], {}
        if kind is not None:
            extra.append("lt.kind = :kind")
            params["kind"] = kind
        return await self._keyset_page(
            session, table="ledger_transactions lt", columns=(
                "lt.id::text AS id, lt.transaction_key, lt.status, lt.kind, "
                " lt.trade_decision_id::text AS trade_decision_id, "
                " lt.execution_id::text AS execution_id, "
                " lt.chain_operation_id::text AS chain_operation_id, "
                " lt.portfolio_namespace, lt.posted_at::text AS posted_at, "
                " lt.created_at::text AS created_at"
            ), sort_col="lt.created_at", direction=direction,
            cursor_st=cursor_st, cursor_id=cursor_id, limit=limit, as_of=as_of,
            extra_where=" AND ".join(extra), params=params,
        )

    async def decision_trace(self, session, decision_id: int) -> list[dict]:
        """Execution trace：decision → intent → execution → envelope/order/trade/position/
        ledger → chain operation（§5.3）。"""
        result = await session.execute(
            text(
                "SELECT 'intent' AS kind, eai.id::text AS id, eai.intent_key AS ref_key, "
                " eai.status AS state, eai.created_at::text AS ts "
                "FROM trading.economic_action_intents eai "
                "WHERE eai.trade_decision_id = :did "
                "UNION ALL "
                "SELECT 'execution', ex.id::text, ex.execution_key, ex.status, "
                " ex.created_at::text FROM trading.executions ex "
                " WHERE ex.economic_action_intent_id IN "
                " (SELECT id FROM trading.economic_action_intents WHERE trade_decision_id = :did) "
                "UNION ALL "
                "SELECT 'envelope', env.id::text, env.envelope_key, env.status, "
                " env.created_at::text FROM trading.execution_authorization_envelopes env "
                " WHERE env.intent_id IN "
                " (SELECT id FROM trading.economic_action_intents WHERE trade_decision_id = :did) "
                "UNION ALL "
                "SELECT 'order', o.id::text, o.order_key, o.status, o.created_at::text "
                " FROM trading.exchange_orders o WHERE o.id IN "
                " (SELECT order_id FROM trading.executions WHERE economic_action_intent_id IN "
                "  (SELECT id FROM trading.economic_action_intents WHERE trade_decision_id = :did) "
                "  AND order_id IS NOT NULL) "
                "UNION ALL "
                "SELECT 'ledger', lt.id::text, lt.transaction_key, lt.kind, "
                " lt.created_at::text FROM trading.ledger_transactions lt "
                " WHERE lt.trade_decision_id = :did "
                "UNION ALL "
                "SELECT 'chain_operation', co.id::text, co.operation_key, co.status, "
                " co.created_at::text FROM trading.chain_operations co "
                " WHERE co.id IN (SELECT chain_operation_id FROM trading.executions "
                "  WHERE economic_action_intent_id IN "
                "  (SELECT id FROM trading.economic_action_intents WHERE trade_decision_id = :did) "
                "  AND chain_operation_id IS NOT NULL) "
                "ORDER BY ts DESC, id DESC LIMIT 200"
            ),
            {"did": decision_id},
        )
        return _rows(result)

    # =====================================================================
    # Model routes
    # =====================================================================

    async def list_model_routes(self, session, *, cursor_st, cursor_id, direction, limit,
                                as_of) -> tuple[list[dict], bool]:
        return await self._keyset_page(
            session, table="model_role_bindings", columns=(
                "id::text AS id, strategy_version_id::text AS strategy_version_id, "
                "role, provider, route, model_ref, network_policy, "
                "binding_version, content_hash, created_at::text AS created_at"
            ), sort_col="created_at", direction=direction, cursor_st=cursor_st,
            cursor_id=cursor_id, limit=limit, as_of=as_of,
        )

    # =====================================================================
    # AI invocations（复合身份 (occurred_at, id)；detail 含 downstream effect）
    # =====================================================================

    _AI_COLS = (
        "ai.id::text AS id, ai.occurred_at::text AS occurred_at, ai.invocation_key, "
        "ai.episode_id::text AS episode_id, ai.stage, ai.role, ai.attempt_no, "
        "ai.requested_provider, ai.requested_route, ai.requested_model, "
        "ai.returned_provider, ai.returned_route, ai.returned_model, "
        "ai.lifecycle_state, ai.terminal_reason, ai.retriable, "
        "ai.input_tokens::text AS input_tokens, ai.cache_tokens::text AS cache_tokens, "
        "ai.output_tokens::text AS output_tokens, "
        "ai.reasoning_tokens::text AS reasoning_tokens, "
        "ai.cost_estimated::text AS cost_estimated, "
        "ai.created_at::text AS created_at"
    )

    async def list_ai(self, session, *, cursor_st, cursor_id, direction, limit, as_of,
                      role=None, lifecycle_state=None) -> tuple[list[dict], bool]:
        extra, params = [], {}
        if role is not None:
            extra.append("ai.role = :role")
            params["role"] = role
        if lifecycle_state is not None:
            extra.append("ai.lifecycle_state = :lifecycle_state")
            params["lifecycle_state"] = lifecycle_state
        return await self._keyset_page(
            session, table="ai_invocations ai", columns=self._AI_COLS,
            sort_col="ai.occurred_at", direction=direction,
            cursor_st=cursor_st, cursor_id=cursor_id, limit=limit, as_of=as_of,
            extra_where=" AND ".join(extra), params=params,
        )

    @staticmethod
    def _parse_occurred_at(value: str):
        from datetime import datetime, timezone

        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            return value  # 交由 DB 判断（fail-closed）

    async def get_ai(self, session, *, occurred_at: str, ai_id: int) -> dict | None:
        oat = self._parse_occurred_at(occurred_at)
        result = await session.execute(
            text(
                "SELECT id::text AS id, occurred_at::text AS occurred_at, invocation_key, "
                " episode_id::text AS episode_id, stage, role, attempt_no, "
                " experiment_variant, parent_invocation_id::text AS parent_invocation_id, "
                " retry_of_invocation_id::text AS retry_of_invocation_id, "
                " fallback_of_invocation_id::text AS fallback_of_invocation_id, "
                " causation_event_id::text AS causation_event_id, "
                " release_manifest_id::text AS release_manifest_id, "
                " strategy_version_id::text AS strategy_version_id, "
                " config_version_id::text AS config_version_id, "
                " model_role_binding_id::text AS model_role_binding_id, "
                " requested_provider, requested_route, requested_model, "
                " returned_provider, returned_route, returned_model, "
                " cache_key_hash, network_policy, context_class, "
                " prompt_version, prompt_artifact_ref, schema_version, "
                " schema_artifact_ref, request_artifact_ref, "
                " raw_response_artifact_ref, parsed_output_artifact_ref, "
                " normalized_output_artifact_ref, lifecycle_state, terminal_reason, "
                " retriable, input_tokens::text AS input_tokens, "
                " cache_tokens::text AS cache_tokens, output_tokens::text AS output_tokens, "
                " reasoning_tokens::text AS reasoning_tokens, tool_count, search_count, "
                " cost_estimated::text AS cost_estimated, cost_currency, "
                " accepted_at::text AS accepted_at, queued_at::text AS queued_at, "
                " started_at::text AS started_at, first_token_at::text AS first_token_at, "
                " response_at::text AS response_at, parsed_at::text AS parsed_at, "
                " validated_at::text AS validated_at, completed_at::text AS completed_at, "
                " created_at::text AS created_at "
                "FROM trading.ai_invocations "
                "WHERE id = :aid AND occurred_at = CAST(:oat AS timestamptz)"
            ),
            {"aid": ai_id, "oat": oat},
        )
        return _one(result)

    async def ai_chain(self, session, *, occurred_at: str, ai_id: int) -> dict:
        """AI detail → model binding/tool/validator/artifact refs/downstream effect（§5.3）。"""
        oat = self._parse_occurred_at(occurred_at)
        invocation = await self.get_ai(session, occurred_at=occurred_at, ai_id=ai_id)
        if invocation is None:
            return {}
        chain: dict = {"invocation": invocation}
        binding = await session.execute(
            text(
                "SELECT id::text AS id, strategy_version_id::text AS strategy_version_id, "
                " role, provider, route, model_ref, network_policy, "
                " binding_version, content_hash "
                "FROM trading.model_role_bindings "
                "WHERE id = :bid"
            ),
            {"bid": invocation.get("model_role_binding_id") or 0},
        )
        chain["model_role_binding"] = _one(binding) if invocation.get("model_role_binding_id") else None
        tools = await session.execute(
            text(
                "SELECT id::text AS id, tool_type, tool_version, status, error_code, "
                " result_artifact_ref, cost::text AS cost, "
                " occurred_at::text AS occurred_at "
                "FROM trading.ai_tool_calls "
                "WHERE invocation_id = :aid AND invocation_occurred_at = CAST(:oat AS timestamptz) "
                "ORDER BY ordinal LIMIT 100"
            ),
            {"aid": ai_id, "oat": oat},
        )
        chain["tool_calls"] = _rows(tools)
        validators = await session.execute(
            text(
                "SELECT id::text AS id, validator_name, validator_version, passed, "
                " severity, reason_code, details_artifact_hash, "
                " occurred_at::text AS occurred_at "
                "FROM trading.ai_validation_results "
                "WHERE invocation_id = :aid AND invocation_occurred_at = CAST(:oat AS timestamptz) "
                "ORDER BY id LIMIT 100"
            ),
            {"aid": ai_id, "oat": oat},
        )
        chain["validations"] = _rows(validators)
        # downstream effect：本 invocation 是否被 retry/fallback 引用
        downstream = await session.execute(
            text(
                "SELECT id::text AS id, retry_of_invocation_id::text AS retry_of, "
                " fallback_of_invocation_id::text AS fallback_of, lifecycle_state, "
                " occurred_at::text AS occurred_at "
                "FROM trading.ai_invocations "
                "WHERE retry_of_invocation_id = :aid OR fallback_of_invocation_id = :aid "
                "LIMIT 20"
            ),
            {"aid": ai_id},
        )
        chain["downstream"] = _rows(downstream)
        return chain

    # =====================================================================
    # Costs
    # =====================================================================

    async def list_costs(self, session, *, cursor_st, cursor_id, direction, limit, as_of,
                         cost_kind=None) -> tuple[list[dict], bool]:
        extra, params = [], {}
        if cost_kind is not None:
            extra.append("oce.cost_kind = :cost_kind")
            params["cost_kind"] = cost_kind
        return await self._keyset_page(
            session, table="operating_cost_entries oce", columns=(
                "oce.id::text AS id, oce.cost_key, oce.cost_kind, "
                "oce.amount::text AS amount, "
                "oce.release_manifest_id::text AS release_manifest_id, "
                "oce.episode_id::text AS episode_id, "
                "oce.period_start::text AS period_start, oce.period_end::text AS period_end, "
                "oce.created_at::text AS created_at"
            ), sort_col="oce.created_at", direction=direction,
            cursor_st=cursor_st, cursor_id=cursor_id, limit=limit, as_of=as_of,
            extra_where=" AND ".join(extra), params=params,
        )

    # =====================================================================
    # Strategy config（只读 runtime_config_versions；不实现 draft/publish）
    # =====================================================================

    async def list_config(self, session, *, cursor_st, cursor_id, direction, limit, as_of,
                          status=None) -> tuple[list[dict], bool]:
        extra, params = [], {}
        if status is not None:
            extra.append("rcv.status = :status")
            params["status"] = status
        return await self._keyset_page(
            session, table="runtime_config_versions rcv", columns=(
                "rcv.id::text AS id, rcv.config_key, rcv.version_no, "
                "rcv.schema_version, rcv.content_hash, rcv.status, rcv.creator, "
                "rcv.created_at::text AS created_at"
            ), sort_col="rcv.created_at", direction=direction,
            cursor_st=cursor_st, cursor_id=cursor_id, limit=limit, as_of=as_of,
            extra_where=" AND ".join(extra), params=params,
        )

    async def get_config(self, session, config_id: int) -> dict | None:
        result = await session.execute(
            text(
                "SELECT id::text AS id, config_key, version_no, "
                " schema_version, content_hash, status, creator, "
                " created_at::text AS created_at "
                "FROM trading.runtime_config_versions WHERE id = :cid"
            ),
            {"cid": config_id},
        )
        return _one(result)

    # =====================================================================
    # Releases（detail 含 exact config/strategy/execution spec/permission/hash）
    # =====================================================================

    async def list_releases(self, session, *, cursor_st, cursor_id, direction, limit, as_of,
                            status=None) -> tuple[list[dict], bool]:
        extra, params = [], {}
        if status is not None:
            extra.append("rm.status = :status")
            params["status"] = status
        return await self._keyset_page(
            session, table="release_manifests rm", columns=(
                "rm.id::text AS id, rm.release_name, rm.git_sha, rm.image_digest, "
                "rm.db_revision, rm.total_hash, rm.status, rm.creator, "
                "rm.created_at::text AS created_at"
            ), sort_col="rm.created_at", direction=direction,
            cursor_st=cursor_st, cursor_id=cursor_id, limit=limit, as_of=as_of,
            extra_where=" AND ".join(extra), params=params,
        )

    async def get_release(self, session, release_id: int) -> dict | None:
        result = await session.execute(
            text(
                "SELECT id::text AS id, release_name, "
                " config_version_id::text AS config_version_id, "
                " strategy_version_id::text AS strategy_version_id, "
                " execution_spec_version_id::text AS execution_spec_version_id, "
                " capital_permission_manifest_id::text AS capital_permission_manifest_id, "
                " git_sha, image_digest, db_revision, total_hash, status, creator, "
                " created_at::text AS created_at "
                "FROM trading.release_manifests WHERE id = :rid"
            ),
            {"rid": release_id},
        )
        return _one(result)

    async def release_chain(self, session, release_id: int) -> dict:
        """Release detail → exact config/strategy/execution spec/permission/hash（§5.3）。"""
        release = await self.get_release(session, release_id)
        if release is None:
            return {}
        chain: dict = {"release": release}
        rows = await session.execute(
            text(
                "SELECT 'config' AS kind, rcv.id::text AS id, rcv.config_key AS ref, "
                " rcv.version_no, rcv.content_hash, rcv.status "
                "FROM trading.runtime_config_versions rcv WHERE rcv.id = :cfg "
                "UNION ALL "
                "SELECT 'strategy', sv.id::text, sv.strategy_key, sv.version_no, "
                " sv.content_hash, sv.status FROM trading.strategy_versions sv "
                " WHERE sv.id = :strat "
                "UNION ALL "
                "SELECT 'execution_spec', esv.id::text, esv.spec_key, esv.version_no, "
                " esv.content_hash, esv.status FROM trading.execution_spec_versions esv "
                " WHERE esv.id = :espec "
                "UNION ALL "
                "SELECT 'capital_permission', cpm.id::text, cpm.name, 0, "
                " cpm.content_hash, cpm.status FROM trading.capital_permission_manifests cpm "
                " WHERE cpm.id = :cperm"
            ),
            {"cfg": release["config_version_id"], "strat": release["strategy_version_id"],
             "espec": release["execution_spec_version_id"],
             "cperm": release["capital_permission_manifest_id"]},
        )
        chain["exact_parts"] = _rows(rows)
        return chain

    # =====================================================================
    # Evaluation —— labels / metrics / promotions
    # =====================================================================

    async def list_labels(self, session, *, cursor_st, cursor_id, direction, limit, as_of,
                          state=None) -> tuple[list[dict], bool]:
        extra, params = [], {}
        if state is not None:
            extra.append("rl.state = :state")
            params["state"] = state
        return await self._keyset_page(
            session, table="resolution_labels rl", columns=(
                "rl.id::text AS id, rl.contract_spec_id::text AS contract_spec_id, "
                "rl.label_key, rl.version_no, rl.state, rl.resolution_state, "
                "rl.policy_code_hash, rl.supersedes_id::text AS supersedes_id, "
                "rl.created_at::text AS created_at"
            ), sort_col="rl.created_at", direction=direction,
            cursor_st=cursor_st, cursor_id=cursor_id, limit=limit, as_of=as_of,
            extra_where=" AND ".join(extra), params=params,
        )

    async def list_metrics(self, session, *, cursor_st, cursor_id, direction, limit, as_of,
                           status=None) -> tuple[list[dict], bool]:
        extra, params = [], {}
        if status is not None:
            extra.append("mr.status = :status")
            params["status"] = status
        return await self._keyset_page(
            session, table="metric_runs mr", columns=(
                "mr.id::text AS id, mr.run_key, mr.split, mr.status, "
                "mr.n_market::text AS n_market, mr.n_episode::text AS n_episode, "
                "mr.n_eff::text AS n_eff, "
                "mr.artifact_hash, mr.release_manifest_id::text AS release_manifest_id, "
                "mr.created_at::text AS created_at"
            ), sort_col="mr.created_at", direction=direction,
            cursor_st=cursor_st, cursor_id=cursor_id, limit=limit, as_of=as_of,
            extra_where=" AND ".join(extra), params=params,
        )

    async def list_promotions(self, session, *, cursor_st, cursor_id, direction, limit, as_of,
                              status=None) -> tuple[list[dict], bool]:
        extra, params = [], {}
        if status is not None:
            extra.append("pd.status = :status")
            params["status"] = status
        return await self._keyset_page(
            session, table="promotion_decisions pd", columns=(
                "pd.id::text AS id, pd.promotion_key, "
                "pd.metric_run_id::text AS metric_run_id, pd.promotion_type, "
                "pd.from_ref, pd.to_ref, pd.evidence_manifest_hash, pd.status, "
                "pd.reason_code, pd.future_effective_at::text AS future_effective_at, "
                "pd.created_at::text AS created_at"
            ), sort_col="pd.created_at", direction=direction,
            cursor_st=cursor_st, cursor_id=cursor_id, limit=limit, as_of=as_of,
            extra_where=" AND ".join(extra), params=params,
        )

    # =====================================================================
    # Replay
    # =====================================================================

    async def list_replay(self, session, *, cursor_st, cursor_id, direction, limit, as_of) -> tuple[list[dict], bool]:
        return await self._keyset_page(
            session, table="replay_runs rr", columns=(
                "rr.id::text AS id, rr.run_key, rr.replay_kind, rr.manifest_hash, "
                "rr.code_hash, rr.seed::text AS seed, rr.input_artifact_hash, rr.output_artifact_hash, "
                "rr.created_at::text AS created_at"
            ), sort_col="rr.created_at", direction=direction,
            cursor_st=cursor_st, cursor_id=cursor_id, limit=limit, as_of=as_of,
        )

    async def get_replay(self, session, replay_id: int) -> dict | None:
        result = await session.execute(
            text(
                "SELECT id::text AS id, run_key, replay_kind, manifest_hash, code_hash, "
                " seed::text AS seed, input_artifact_hash, output_artifact_hash, "
                " created_at::text AS created_at "
                "FROM trading.replay_runs WHERE id = :rid"
            ),
            {"rid": replay_id},
        )
        return _one(result)

    # =====================================================================
    # Integrity —— alerts / workflows / external calls / artifact lineage
    # =====================================================================

    async def list_alerts(self, session, *, cursor_st, cursor_id, direction, limit, as_of,
                          severity=None) -> tuple[list[dict], bool]:
        extra, params = [], {}
        if severity is not None:
            extra.append("ae.severity = :severity")
            params["severity"] = severity
        return await self._keyset_page(
            session, table="alert_events ae", columns=(
                "ae.id::text AS id, ae.alert_key, ae.severity, ae.code, "
                "ae.message_redacted, ae.created_at::text AS created_at"
            ), sort_col="ae.created_at", direction=direction,
            cursor_st=cursor_st, cursor_id=cursor_id, limit=limit, as_of=as_of,
            extra_where=" AND ".join(extra), params=params,
        )

    async def list_workflows(self, session, *, aggregate_type: str, aggregate_id: str,
                             cursor_st, cursor_id, direction, limit, as_of) -> tuple[list[dict], bool]:
        # aggregate_type 固定 allowlist（不允许任意字符串扩展查询面）
        if aggregate_type not in {"episode", "decision", "intent", "chain_operation",
                                  "forecast_submission", "evidence_bundle"}:
            raise ValueError(f"workflow aggregate_type not allowed: {aggregate_type!r}")
        return await self._keyset_page(
            session, table="workflow_events we", columns=(
                "we.id::text AS id, we.event_key, we.event_type, "
                "we.aggregate_type, we.aggregate_id, we.payload_hash, "
                "we.created_at::text AS created_at"
            ), sort_col="we.created_at", direction=direction,
            cursor_st=cursor_st, cursor_id=cursor_id, limit=limit, as_of=as_of,
            extra_where="we.aggregate_type = :agg_type AND we.aggregate_id = :agg_id",
            params={"agg_type": aggregate_type, "agg_id": aggregate_id},
        )

    async def integrity_chain(self, session, *, aggregate_type: str, aggregate_id: str,
                              limit: int = 200) -> dict:
        """Integrity timeline → workflow/outbox/external-call/alert/artifact lineage（§5.3）。"""
        if aggregate_type not in {"episode", "decision", "intent", "chain_operation",
                                  "forecast_submission", "evidence_bundle"}:
            raise ValueError(f"workflow aggregate_type not allowed: {aggregate_type!r}")
        chain: dict = {}
        workflows = await session.execute(
            text(
                "SELECT id::text AS id, event_key, event_type, payload_hash, "
                " created_at::text AS created_at "
                "FROM trading.workflow_events "
                "WHERE aggregate_type = :agg_type AND aggregate_id = :agg_id "
                "ORDER BY id DESC LIMIT :lim"
            ),
            {"agg_type": aggregate_type, "agg_id": aggregate_id, "lim": limit},
        )
        chain["workflows"] = _rows(workflows)
        outbox = await session.execute(
            text(
                "SELECT id::text AS id, event_id, topic, status, attempt, "
                " available_at::text AS available_at, deadline::text AS deadline, "
                " aggregate_type, aggregate_id "
                "FROM trading.transactional_outbox "
                "WHERE aggregate_type = :agg_type AND aggregate_id = :agg_id "
                "ORDER BY id DESC LIMIT :lim"
            ),
            {"agg_type": aggregate_type, "agg_id": aggregate_id, "lim": limit},
        )
        chain["outbox"] = _rows(outbox)
        calls = await session.execute(
            text(
                "SELECT id::text AS id, attempt_key, driver, endpoint, method, "
                " request_hash, response_hash, status_code, latency_ms, "
                " created_at::text AS created_at "
                "FROM trading.external_call_attempts "
                "WHERE id IN (SELECT id FROM trading.workflow_events "
                " WHERE aggregate_type = :agg_type AND aggregate_id = :agg_id "
                " AND event_type LIKE 'external_call%') "
                "ORDER BY id DESC LIMIT :lim"
            ),
            {"agg_type": aggregate_type, "agg_id": aggregate_id, "lim": limit},
        )
        chain["external_calls"] = _rows(calls)
        alerts = await session.execute(
            text(
                "SELECT id::text AS id, alert_key, severity, code, message_redacted, "
                " created_at::text AS created_at "
                "FROM trading.alert_events "
                "WHERE code ILIKE '%' || :agg_type || '%' AND message_redacted ILIKE '%' || :agg_id || '%' "
                "ORDER BY id DESC LIMIT :lim"
            ),
            {"agg_type": aggregate_type, "agg_id": aggregate_id, "lim": limit},
        )
        chain["alerts"] = _rows(alerts)
        return chain

    # =====================================================================
    # Artifacts —— metadata（content 分离）+ lineage
    # =====================================================================

    async def artifact_metadata(self, session, content_hash: str) -> dict | None:
        result = await session.execute(
            text(
                "SELECT ao.sha256 AS content_hash, ao.mime AS content_type, "
                " ao.original_size::text AS content_length, "
                " ao.stored_size::text AS stored_size, "
                " ao.compression, ao.storage_driver, ao.storage_version, "
                " ao.created_at::text AS stored_at "
                "FROM trading.artifact_objects ao "
                "WHERE ao.sha256 = :sha LIMIT 1"
            ),
            {"sha": content_hash},
        )
        return _one(result)

    async def artifact_lineage(self, session, content_hash: str, limit: int = 50) -> list[dict]:
        result = await session.execute(
            text(
                "SELECT le.id::text AS id, "
                " le.from_artifact_id::text AS from_artifact_id, "
                " le.to_artifact_id::text AS to_artifact_id, "
                " le.relation, le.invocation_ref, le.created_at::text AS created_at "
                "FROM trading.artifact_lineage_edges le "
                "WHERE le.from_artifact_id IN (SELECT id FROM trading.artifact_objects "
                " WHERE sha256 = :sha) OR le.to_artifact_id IN (SELECT id FROM "
                " trading.artifact_objects WHERE sha256 = :sha) "
                "ORDER BY le.id DESC LIMIT :lim"
            ),
            {"sha": content_hash, "lim": limit},
        )
        return _rows(result)

    async def is_ai_artifact(self, session, content_hash: str) -> bool:
        """AI request/raw/parsed artifact 判定（附加 v2:ai:artifact 权限）。"""
        result = await session.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM trading.ai_invocations "
                " WHERE request_artifact_ref = :sha OR raw_response_artifact_ref = :sha "
                "    OR parsed_output_artifact_ref = :sha OR normalized_output_artifact_ref = :sha) "
                "OR EXISTS (SELECT 1 FROM trading.ai_tool_calls "
                " WHERE result_artifact_ref = :sha) "
                "OR EXISTS (SELECT 1 FROM trading.ai_validation_results "
                " WHERE details_artifact_hash = :sha)"
            ),
            {"sha": content_hash},
        )
        return bool(result.scalar_one())
