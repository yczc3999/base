"""admin page menus

Revision ID: b1000071
Revises: b1000070
Create Date: 2026-08-12 00:00:00

v2_0071 —— WP-07B Checkpoint A：Admin 菜单页与详情页 seed。

- ``upgrade``：
  - 在 0070 不可见目录 ``v2-admin`` 下创建 14 个 type=1 菜单页（挂对应 ``v2:*:view``
    BUTTON 权限）+ 5 个 type=1 隐藏详情路由（is_visible=false，path 含 ``:id``）+
    Artifacts 隐藏路由。不写 role_menus（不隐式授权普通角色）；不依赖固定 menu ID
    （parent_id 引用 0070 目录实际 id）。
  - slug/perms 冲突但内容不全等时 RAISE（seed 唯一性 fail-closed）；空库（无 Base
    menus）→ 菜单 seed 静默跳过（同 0070 门控）。
- ``downgrade``：fail-closed preflight（存在 role_menus 绑定到 0071 菜单 → 整次拒绝；
  0071 目标菜单缺失或 trading 未知表出现 → 拒绝），DELETE 0071 菜单行。不修改任何
  trading facts 与 0070 权限。

fixed literal DDL；不 import live ORM 生成 DDL；无密钥/signature 明文。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "b1000071"
down_revision: Union[str, Sequence[str], None] = "b1000070"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_MENU_DIR_SLUG = "v2-admin"

# (slug, label, path, template_path, perms, is_visible, sort)
_PAGES = (
    ("v2-page-dashboard", "Dashboard", "/v2/dashboard", "v2/dashboard/index",
     "v2:dashboard:view", True, 1),
    ("v2-page-markets", "Markets", "/v2/markets", "v2/markets/index",
     "v2:markets:view", True, 2),
    ("v2-page-components", "Components", "/v2/components", "v2/components/index",
     "v2:components:view", True, 3),
    ("v2-page-episodes", "Episodes", "/v2/episodes", "v2/episodes/index",
     "v2:episodes:view", True, 4),
    ("v2-page-decisions", "Decisions", "/v2/decisions", "v2/decisions/index",
     "v2:decisions:view", True, 5),
    ("v2-page-execution", "Execution", "/v2/execution", "v2/execution/index",
     "v2:execution:view", True, 6),
    ("v2-page-models-ai", "Models & AI", "/v2/models-ai", "v2/models-ai/index",
     "v2:models:view", True, 7),
    ("v2-page-ai-invocations", "AI Invocations", "/v2/ai-invocations",
     "v2/ai-invocations/index", "v2:ai:view", True, 8),
    ("v2-page-costs", "Costs", "/v2/costs", "v2/costs/index",
     "v2:costs:view", True, 9),
    ("v2-page-config", "Strategy Config", "/v2/config", "v2/config/index",
     "v2:config:view", True, 10),
    ("v2-page-releases", "Releases", "/v2/releases", "v2/releases/index",
     "v2:release:view", True, 11),
    ("v2-page-evaluation", "Evaluation", "/v2/evaluation", "v2/evaluation/index",
     "v2:evaluation:view", True, 12),
    ("v2-page-replay", "Replay", "/v2/replay", "v2/replay/index",
     "v2:replay:view", True, 13),
    ("v2-page-integrity", "Integrity", "/v2/integrity", "v2/integrity/index",
     "v2:integrity:view", True, 14),
    # 隐藏详情页（is_visible=false；不占侧边菜单）
    ("v2-page-market-detail", "Market Detail", "/v2/markets/:id",
     "v2/markets/detail", "v2:markets:view", False, 101),
    ("v2-page-component-detail", "Component Detail", "/v2/components/:id",
     "v2/components/detail", "v2:components:view", False, 102),
    ("v2-page-episode-detail", "Episode Detail", "/v2/episodes/:id",
     "v2/episodes/detail", "v2:episodes:view", False, 103),
    ("v2-page-decision-detail", "Decision Detail", "/v2/decisions/:id",
     "v2/decisions/detail", "v2:decisions:view", False, 104),
    ("v2-page-ai-detail", "AI Invocation Detail", "/v2/ai-invocations/:id",
     "v2/ai-invocations/detail", "v2:ai:view", False, 105),
    ("v2-page-artifacts", "Artifacts", "/v2/artifacts/:content_hash",
     "v2/artifacts/detail", "v2:artifact:read", False, 106),
)

_ALL_SLUGS = ", ".join(f"'{slug}'" for slug, *_ in _PAGES)


def _sql_path(path: str) -> str:
    """Render route paths without ``:name`` being parsed as SQL bind params."""
    if ":" not in path:
        return f"'{path}'"
    prefix, parameter = path.split(":", 1)
    return f"'{prefix}' || chr(58) || '{parameter}'"


_PAGE_VALUES_SQL = ",\n            ".join(
    "('{slug}','{label}',{path},'{template}','{perms}',{visible},{sort})".format(
        slug=slug,
        label=label,
        path=_sql_path(path),
        template=template_path,
        perms=perms,
        visible=str(is_visible).lower(),
        sort=sort,
    )
    for slug, label, path, template_path, perms, is_visible, sort in _PAGES
)

_EXACT_PAGE_MATCHES = " OR ".join(
    "(m.slug = '{slug}' AND m.parent_id = v2_dir AND m.type = 1 "
    "AND m.label = '{label}' AND m.path = {path} "
    "AND m.template_path = '{template}' AND m.perms = '{perms}' "
    "AND m.is_visible = {visible} AND m.sort = {sort} AND m.status = 1)".format(
        slug=slug,
        label=label,
        path=_sql_path(path),
        template=template_path,
        perms=perms,
        visible=str(is_visible).lower(),
        sort=sort,
    )
    for slug, label, path, template_path, perms, is_visible, sort in _PAGES
)

# b1000070 已存在的 trading 关系（0071 downgrade preflight allowlist）。
_PRE_0071_RELATIONS = (
    "artifact_objects", "artifact_lineage_edges", "archive_manifests",
    "retention_manifests", "runtime_config_versions", "strategy_objective_contracts",
    "strategy_versions", "execution_spec_versions", "capital_permission_manifests",
    "model_role_bindings", "release_manifests", "policy_type_scopes", "policy_freezes",
    "secret_vault_entries", "secret_vault_versions", "secret_access_events",
    "idempotency_claims", "transactional_outbox", "outbox_delivery_history",
    "job_completions", "pm_universe_frames", "pm_universe_frame_pages", "pm_events",
    "pm_markets", "pm_market_versions", "pm_tokens", "pm_token_versions",
    "pm_market_lifecycle_events", "pm_market_current", "pm_connection_epochs",
    "pm_source_event_batches", "pm_source_event_index", "pm_book_checkpoints",
    "pm_book_levels", "pm_book_current", "pm_quote_bindings", "contract_snapshots",
    "contract_specs", "payout_functions", "forecast_components", "world_schema_versions",
    "forecast_component_versions", "forecast_component_contract_specs",
    "portfolio_dependency_edges", "evaluation_cohorts", "universe_memberships",
    "screening_episodes", "audit_samples", "decision_opportunities",
    "decision_opportunity_markets", "episode_memberships", "forecast_episodes",
    "episode_contract_specs", "information_snapshots", "information_snapshot_items",
    "gate_decisions", "priors", "evidence_coverage_policies", "evidence_revisions",
    "evidence_bundles", "evidence_bundle_items", "forecast_input_manifests",
    "forecast_submissions", "payout_projections", "coherence_checks",
    "forecast_challenges", "forecast_leases", "ai_invocations", "ai_tool_calls",
    "ai_validation_results", "market_relative_decisions", "discrepancy_reviews",
    "trade_decisions", "action_candidates", "resolution_cashflows", "action_sets",
    "action_set_legs", "underwriting_plans", "economic_action_intents",
    "executions", "positions", "position_lots", "ledger_transactions",
    "ledger_postings", "operating_cost_entries",
    "resolution_labels", "resolution_clusters", "resolution_cluster_memberships",
    "score_targets", "score_target_memberships", "score_observations", "experiments",
    "experiment_variants", "challenger_variants", "metric_runs", "error_reviews",
    "ablation_runs", "promotion_decisions", "replay_runs",
    "ops_health_current", "pipeline_funnel_hourly", "account_risk_current",
    "provider_cost_daily", "latest_chain_summary",
    "pm_accounts", "pm_balance_allowance_snapshots", "account_funds_current",
    "capital_reservations", "execution_leases",
    "execution_authorization_envelopes", "exchange_order_attempts", "exchange_orders",
    "order_state_events", "exchange_trades", "account_reconciliations",
    "workflow_events", "external_call_attempts", "alert_events",
    "contract_registry", "chain_operations", "chain_operation_state_history",
    "settlement_observations",
)


def _assert_downgrade_safe() -> None:
    allowed = ", ".join(f"'{name}'" for name in _PRE_0071_RELATIONS)
    op.execute(
        f"""
DO $v2_wp07b_menu_preflight$
DECLARE bound_count bigint;
        missing_menu text;
        unknown_objects text;
        tampered_menu text;
        v2_dir bigint;
BEGIN
    -- 存在 role_menus 绑定到 0071 菜单 → 整次拒绝
    IF to_regclass('public.menus') IS NOT NULL AND to_regclass('public.role_menus') IS NOT NULL THEN
        SELECT count(*) INTO bound_count
          FROM public.role_menus rm
          JOIN public.menus m ON m.id = rm.menu_id
         WHERE m.slug IN ({_ALL_SLUGS});
        IF bound_count <> 0 THEN
            RAISE EXCEPTION 'v2_wp07b_role_menu_bound:%', bound_count USING ERRCODE='55000';
        END IF;
    END IF;
    -- 0071 目标菜单必须全部存在
    IF to_regclass('public.menus') IS NOT NULL THEN
        SELECT id INTO v2_dir FROM public.menus WHERE slug = '{_MENU_DIR_SLUG}';
        IF v2_dir IS NULL THEN
            RAISE EXCEPTION 'v2_wp07b_parent_missing:{_MENU_DIR_SLUG}' USING ERRCODE='55000';
        END IF;
        SELECT string_agg(name, ', ' ORDER BY name) INTO missing_menu FROM (
            SELECT unnest(ARRAY[{_ALL_SLUGS}]) AS name
            EXCEPT
            SELECT m.slug FROM public.menus m WHERE m.slug IN ({_ALL_SLUGS})
        ) x;
        IF missing_menu IS NOT NULL THEN
            RAISE EXCEPTION 'v2_wp07b_menu_missing:%', missing_menu USING ERRCODE='55000';
        END IF;
        -- 任一受管字段被篡改（含 slug↔permission 互换）均拒绝。
        SELECT string_agg(m.slug, ', ' ORDER BY m.slug) INTO tampered_menu
          FROM public.menus m
         WHERE m.slug IN ({_ALL_SLUGS})
           AND NOT ({_EXACT_PAGE_MATCHES});
        IF tampered_menu IS NOT NULL THEN
            RAISE EXCEPTION 'v2_wp07b_menu_tampered:%', tampered_menu USING ERRCODE='55000';
        END IF;
    END IF;
    -- trading 出现未知关系 → fail-closed
    SELECT string_agg(c.relname, ', ' ORDER BY c.relname)
      INTO unknown_objects
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = 'trading'
       AND c.relkind IN ('r','p','v','m','f')
       AND c.relname NOT IN ({allowed})
       AND NOT EXISTS (
           SELECT 1 FROM pg_inherits i
           JOIN pg_class p ON p.oid = i.inhparent
           JOIN pg_namespace pn ON pn.oid = p.relnamespace
           WHERE i.inhrelid = c.oid AND pn.nspname = 'trading'
             AND ((p.relname = 'outbox_delivery_history'
                   AND c.relname ~ '^outbox_delivery_history_[0-9]{{6}}$')
               OR (p.relname IN ('pm_source_event_batches','pm_source_event_index',
                                  'pm_book_checkpoints','pm_book_levels')
                   AND c.relname ~ ('^' || p.relname || '_[0-9]{{8}}$'))
               OR (p.relname IN ('ai_invocations','ai_tool_calls','ai_validation_results')
                   AND c.relname ~ ('^' || p.relname || '_[0-9]{{6}}$')))
       );
    IF unknown_objects IS NOT NULL THEN
        RAISE EXCEPTION 'v2_wp07b_unknown_object:%', unknown_objects
        USING ERRCODE='55000';
    END IF;
END $v2_wp07b_menu_preflight$
"""
    )


def upgrade() -> None:
    # menus 表存在才 seed（同 0070 门控）
    op.execute(
        f"""
DO $v2_wp07b_seed$
DECLARE v2_dir bigint;
BEGIN
    IF to_regclass('public.menus') IS NULL THEN
        RETURN;
    END IF;
    SELECT id INTO v2_dir FROM public.menus WHERE slug = '{_MENU_DIR_SLUG}';
    IF v2_dir IS NULL THEN
        RAISE EXCEPTION 'v2_wp07b_parent_missing:{_MENU_DIR_SLUG}' USING ERRCODE='55000';
    END IF;
    -- 14 菜单页 + 5 隐藏详情 + artifacts 隐藏路由
    INSERT INTO public.menus (parent_id, type, slug, label, path, template_path, perms,
                              is_visible, sort, status)
    SELECT v2_dir, 1, v.slug, v.label, v.path, v.template_path, v.perms, v.is_visible, v.sort, 1
      FROM (VALUES
            {_PAGE_VALUES_SQL}
      ) AS v(slug, label, path, template_path, perms, is_visible, sort)
     WHERE NOT EXISTS (SELECT 1 FROM public.menus m WHERE m.slug = v.slug);
    -- 受管 slug 必须逐项精确对应 label/route/permission/可见性/排序。
    IF EXISTS (
        SELECT 1 FROM public.menus m
         WHERE m.slug IN ({_ALL_SLUGS})
           AND NOT ({_EXACT_PAGE_MATCHES})
    ) THEN
        RAISE EXCEPTION 'v2_wp07b_menu_slug_conflict' USING ERRCODE='55000';
    END IF;
END $v2_wp07b_seed$
"""
    )


def downgrade() -> None:
    _assert_downgrade_safe()
    op.execute(
        f"""
DO $v2_wp07b_cleanup$
BEGIN
    IF to_regclass('public.menus') IS NOT NULL THEN
        DELETE FROM public.menus WHERE slug IN ({_ALL_SLUGS});
    END IF;
END $v2_wp07b_cleanup$
"""
    )
