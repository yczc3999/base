"""admin read permissions and keyset indexes

Revision ID: b1000070
Revises: b1000052
Create Date: 2026-08-12 00:00:00

v2_0070 —— WP-07A Checkpoint A：Admin Read API 权限目录与 keyset 查询索引。

- ``upgrade``：
  - 创建 1 个不可见权限目录（``menus.type=0``、``is_visible=false``，slug ``v2-admin``）
    与 16 个 BUTTON 权限（``menus.type=2``，perms ``v2:*:view`` / ``v2:ai:artifact`` /
    ``v2:artifact:read``）。不创建业务页面菜单；不自动授予普通角色（不写 role_menus）；
    超级管理员仍按 Base 规则绕过。
  - slug/perms 冲突但内容不全等时 RAISE（seed 唯一性 fail-closed）；不依赖固定 menu ID
    （目录 id 由 RETURNING 取得，BUTTON parent_id 引用实际 id）。
  - 为 keyset 分页查询创建 17 个 ``(sort_key, id)`` composite index（名称/列序与
    admin_read SQL query 一一对应；不预建 WP-08 通用归档索引）。
- ``downgrade``：fail-closed preflight（存在 role_menus 绑定到 0070 权限菜单 → 整次拒绝；
  0070 目标 index 缺失或 trading 未知表出现 → 拒绝），drop 17 个 index，DELETE 0070
  权限行与目录。不修改任何 trading facts。

fixed literal DDL；不 import live ORM 生成 DDL；无密钥/signature 明文。
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1000070"
down_revision: Union[str, Sequence[str], None] = "b1000052"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 不可见权限目录（slug 唯一；label 仅供运维可读）
_V2_MENU_DIR_SLUG = "v2-admin"
_V2_MENU_DIR_LABEL = "V2 Admin Read"

# 16 个 BUTTON 权限：(slug, label, perms, 是否 AI artifact 附加)
_V2_PERMISSIONS = (
    ("v2-dashboard-view", "Dashboard", "v2:dashboard:view"),
    ("v2-markets-view", "Markets", "v2:markets:view"),
    ("v2-components-view", "Components", "v2:components:view"),
    ("v2-episodes-view", "Episodes", "v2:episodes:view"),
    ("v2-decisions-view", "Decisions", "v2:decisions:view"),
    ("v2-execution-view", "Execution", "v2:execution:view"),
    ("v2-models-view", "Models", "v2:models:view"),
    ("v2-ai-view", "AI Invocations", "v2:ai:view"),
    ("v2-ai-artifact", "AI Artifacts", "v2:ai:artifact"),
    ("v2-costs-view", "Costs", "v2:costs:view"),
    ("v2-config-view", "Strategy Config", "v2:config:view"),
    ("v2-release-view", "Releases", "v2:release:view"),
    ("v2-evaluation-view", "Evaluation", "v2:evaluation:view"),
    ("v2-replay-view", "Replay", "v2:replay:view"),
    ("v2-integrity-view", "Integrity", "v2:integrity:view"),
    ("v2-artifact-read", "Artifacts", "v2:artifact:read"),
)

# keyset composite index：(index_name, table, columns) —— 与 admin_read 查询一一对应。
_KEYSET_INDEXES = (
    ("ix_v2_admin_pm_markets_keyset", "pm_markets", "(created_at, id)"),
    ("ix_v2_admin_forecast_episodes_keyset", "forecast_episodes", "(created_at, id)"),
    ("ix_v2_admin_ai_invocations_keyset", "ai_invocations", "(occurred_at, id)"),
    ("ix_v2_admin_trade_decisions_keyset", "trade_decisions", "(created_at, id)"),
    ("ix_v2_admin_intents_keyset", "economic_action_intents", "(created_at, id)"),
    ("ix_v2_admin_exchange_orders_keyset", "exchange_orders", "(created_at, id)"),
    ("ix_v2_admin_ledger_keyset", "ledger_transactions", "(created_at, id)"),
    ("ix_v2_admin_costs_keyset", "operating_cost_entries", "(created_at, id)"),
    ("ix_v2_admin_runtime_config_keyset", "runtime_config_versions", "(created_at, id)"),
    ("ix_v2_admin_release_keyset", "release_manifests", "(created_at, id)"),
    ("ix_v2_admin_resolution_labels_keyset", "resolution_labels", "(created_at, id)"),
    ("ix_v2_admin_metric_runs_keyset", "metric_runs", "(created_at, id)"),
    ("ix_v2_admin_promotions_keyset", "promotion_decisions", "(created_at, id)"),
    ("ix_v2_admin_replay_keyset", "replay_runs", "(created_at, id)"),
    ("ix_v2_admin_alerts_keyset", "alert_events", "(created_at, id)"),
    ("ix_v2_admin_workflow_keyset", "workflow_events", "(created_at, id)"),
    ("ix_v2_admin_external_calls_keyset", "external_call_attempts", "(created_at, id)"),
)

_KEYSET_INDEX_NAMES = ", ".join(f"'{name}'" for name, _t, _c in _KEYSET_INDEXES)
_ALL_SLUGS = ", ".join(
    f"'{slug}'" for slug, _label, _perms in ((_V2_MENU_DIR_SLUG, "", ""), *_V2_PERMISSIONS)
)
_ALL_PERMS = ", ".join(f"'{perms}'" for _slug, _label, perms in _V2_PERMISSIONS)


# b1000052 已存在的 trading 关系（0070 downgrade preflight allowlist）。
_PRE_0070_RELATIONS = (
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
    allowed = ", ".join(f"'{name}'" for name in _PRE_0070_RELATIONS)
    op.execute(
        f"""
DO $v2_wp07a_admin_read_preflight$
DECLARE bound_count bigint;
        missing_index text;
        unknown_objects text;
        tampered_menu text;
BEGIN
    -- 存在 role_menus 绑定到 0070 权限菜单 → 整次拒绝（回滚前必须先撤销 V2 role bindings）
    -- （menus/role_menus 可能不存在于空库；仅当存在时检查）
    IF to_regclass('public.menus') IS NOT NULL AND to_regclass('public.role_menus') IS NOT NULL THEN
        SELECT count(*) INTO bound_count
          FROM public.role_menus rm
          JOIN public.menus m ON m.id = rm.menu_id
         WHERE m.slug IN ({_ALL_SLUGS});
        IF bound_count <> 0 THEN
            RAISE EXCEPTION 'v2_wp07a_role_menu_bound:%', bound_count USING ERRCODE='55000';
        END IF;
    END IF;
    -- 0070 目标 index 必须全部存在（缺失即拒绝）
    SELECT string_agg(i.indexname, ', ' ORDER BY i.indexname)
      INTO missing_index
      FROM pg_indexes i
     WHERE i.schemaname = 'trading'
       AND i.indexname NOT IN ({_KEYSET_INDEX_NAMES})
       AND i.indexname LIKE 'ix_v2_admin_%';
    -- 反向：要求 17 个 index 都存在
    SELECT string_agg(name, ', ' ORDER BY name) INTO missing_index FROM (
        SELECT unnest(ARRAY[{_KEYSET_INDEX_NAMES}]) AS name
        EXCEPT
        SELECT i.indexname FROM pg_indexes i WHERE i.schemaname = 'trading'
    ) x;
    IF missing_index IS NOT NULL THEN
        RAISE EXCEPTION 'v2_wp07a_keyset_index_missing:%', missing_index
        USING ERRCODE='55000';
    END IF;
    -- trading 出现未知关系（非 0052 allowlist；动态分区子表白名单豁免）→ fail-closed
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
        RAISE EXCEPTION 'v2_wp07a_unknown_object:%', unknown_objects
        USING ERRCODE='55000';
    END IF;
    -- public.menus 0070 权限行内容被篡改（label/perms/type/parent 与 seed 定义不符）→ 拒绝
    IF to_regclass('public.menus') IS NOT NULL THEN
        SELECT string_agg(m.slug, ', ' ORDER BY m.slug)
          INTO tampered_menu
          FROM public.menus m
         WHERE m.slug IN ('v2-admin', 'v2-dashboard-view', 'v2-markets-view', 'v2-components-view', 'v2-episodes-view', 'v2-decisions-view', 'v2-execution-view', 'v2-models-view', 'v2-ai-view', 'v2-ai-artifact', 'v2-costs-view', 'v2-config-view', 'v2-release-view', 'v2-evaluation-view', 'v2-replay-view', 'v2-integrity-view', 'v2-artifact-read')
           AND NOT ((m.slug = 'v2-admin' AND m.type = 0 AND m.is_visible = false AND m.label = 'V2 Admin Read') OR (m.slug = 'v2-dashboard-view' AND m.type = 2 AND m.is_visible = false AND m.label = 'Dashboard' AND m.perms = 'v2:dashboard:view' AND m.parent_id = (SELECT id FROM public.menus d WHERE d.slug = 'v2-admin')) OR (m.slug = 'v2-markets-view' AND m.type = 2 AND m.is_visible = false AND m.label = 'Markets' AND m.perms = 'v2:markets:view' AND m.parent_id = (SELECT id FROM public.menus d WHERE d.slug = 'v2-admin')) OR (m.slug = 'v2-components-view' AND m.type = 2 AND m.is_visible = false AND m.label = 'Components' AND m.perms = 'v2:components:view' AND m.parent_id = (SELECT id FROM public.menus d WHERE d.slug = 'v2-admin')) OR (m.slug = 'v2-episodes-view' AND m.type = 2 AND m.is_visible = false AND m.label = 'Episodes' AND m.perms = 'v2:episodes:view' AND m.parent_id = (SELECT id FROM public.menus d WHERE d.slug = 'v2-admin')) OR (m.slug = 'v2-decisions-view' AND m.type = 2 AND m.is_visible = false AND m.label = 'Decisions' AND m.perms = 'v2:decisions:view' AND m.parent_id = (SELECT id FROM public.menus d WHERE d.slug = 'v2-admin')) OR (m.slug = 'v2-execution-view' AND m.type = 2 AND m.is_visible = false AND m.label = 'Execution' AND m.perms = 'v2:execution:view' AND m.parent_id = (SELECT id FROM public.menus d WHERE d.slug = 'v2-admin')) OR (m.slug = 'v2-models-view' AND m.type = 2 AND m.is_visible = false AND m.label = 'Models' AND m.perms = 'v2:models:view' AND m.parent_id = (SELECT id FROM public.menus d WHERE d.slug = 'v2-admin')) OR (m.slug = 'v2-ai-view' AND m.type = 2 AND m.is_visible = false AND m.label = 'AI Invocations' AND m.perms = 'v2:ai:view' AND m.parent_id = (SELECT id FROM public.menus d WHERE d.slug = 'v2-admin')) OR (m.slug = 'v2-ai-artifact' AND m.type = 2 AND m.is_visible = false AND m.label = 'AI Artifacts' AND m.perms = 'v2:ai:artifact' AND m.parent_id = (SELECT id FROM public.menus d WHERE d.slug = 'v2-admin')) OR (m.slug = 'v2-costs-view' AND m.type = 2 AND m.is_visible = false AND m.label = 'Costs' AND m.perms = 'v2:costs:view' AND m.parent_id = (SELECT id FROM public.menus d WHERE d.slug = 'v2-admin')) OR (m.slug = 'v2-config-view' AND m.type = 2 AND m.is_visible = false AND m.label = 'Strategy Config' AND m.perms = 'v2:config:view' AND m.parent_id = (SELECT id FROM public.menus d WHERE d.slug = 'v2-admin')) OR (m.slug = 'v2-release-view' AND m.type = 2 AND m.is_visible = false AND m.label = 'Releases' AND m.perms = 'v2:release:view' AND m.parent_id = (SELECT id FROM public.menus d WHERE d.slug = 'v2-admin')) OR (m.slug = 'v2-evaluation-view' AND m.type = 2 AND m.is_visible = false AND m.label = 'Evaluation' AND m.perms = 'v2:evaluation:view' AND m.parent_id = (SELECT id FROM public.menus d WHERE d.slug = 'v2-admin')) OR (m.slug = 'v2-replay-view' AND m.type = 2 AND m.is_visible = false AND m.label = 'Replay' AND m.perms = 'v2:replay:view' AND m.parent_id = (SELECT id FROM public.menus d WHERE d.slug = 'v2-admin')) OR (m.slug = 'v2-integrity-view' AND m.type = 2 AND m.is_visible = false AND m.label = 'Integrity' AND m.perms = 'v2:integrity:view' AND m.parent_id = (SELECT id FROM public.menus d WHERE d.slug = 'v2-admin')) OR (m.slug = 'v2-artifact-read' AND m.type = 2 AND m.is_visible = false AND m.label = 'Artifacts' AND m.perms = 'v2:artifact:read' AND m.parent_id = (SELECT id FROM public.menus d WHERE d.slug = 'v2-admin')));
        IF tampered_menu IS NOT NULL THEN
            RAISE EXCEPTION 'v2_wp07a_menu_tampered:%', tampered_menu USING ERRCODE='55000';
        END IF;
    END IF;
END $v2_wp07a_admin_read_preflight$
"""
    )


def _seed_permissions() -> None:
    """创建不可见目录 + 16 个 BUTTON 权限；slug/perms 冲突但内容不全等时 RAISE。

    menus 表由 Base init.sql 建立，空库（无 Base 平台）时权限 seed 静默跳过
    （真实部署 Base init.sql 先建）；slug/perms 冲突校验仅在 menus 存在时生效。
    """
    perm_values = ",\n".join(
        f"    ('{slug}', 2, '{label}', '{perms}')"
        for slug, label, perms in _V2_PERMISSIONS
    )
    op.execute(
        f"""
DO $v2_wp07a_seed$
DECLARE v2_dir bigint;
BEGIN
    IF to_regclass('public.menus') IS NULL THEN
        RETURN;  -- 空库/无 Base 平台：仅建 index，权限 seed 由部署方 Base init.sql 承载
    END IF;
    -- 目录：slug 唯一；内容冲突 fail-closed
    INSERT INTO public.menus (parent_id, type, slug, label, is_visible, sort, status)
    SELECT 0, 0, '{_V2_MENU_DIR_SLUG}', '{_V2_MENU_DIR_LABEL}', false, 0, 1
    WHERE NOT EXISTS (SELECT 1 FROM public.menus WHERE slug = '{_V2_MENU_DIR_SLUG}');
    IF EXISTS (
        SELECT 1 FROM public.menus
         WHERE slug = '{_V2_MENU_DIR_SLUG}'
           AND NOT (type = 0 AND is_visible = false AND label = '{_V2_MENU_DIR_LABEL}')
    ) THEN
        RAISE EXCEPTION 'v2_wp07a_menu_slug_conflict:{_V2_MENU_DIR_SLUG}'
        USING ERRCODE='55000';
    END IF;
    SELECT id INTO v2_dir FROM public.menus WHERE slug = '{_V2_MENU_DIR_SLUG}';
    -- 16 个 BUTTON 权限（parent_id 引用实际目录 id，不依赖固定 ID）
    INSERT INTO public.menus (parent_id, type, slug, label, perms, is_visible, sort, status)
    SELECT v2_dir, 2, v.slug, v.label, v.perms, false, 0, 1
      FROM (VALUES
{perm_values}
           ) AS v(slug, type, label, perms)
     WHERE NOT EXISTS (SELECT 1 FROM public.menus m WHERE m.slug = v.slug);
    -- 冲突但内容不全等 → fail
    IF EXISTS (
        SELECT 1 FROM public.menus m
         WHERE m.slug IN ({_ALL_SLUGS})
           AND NOT (
               (m.slug = '{_V2_MENU_DIR_SLUG}' AND m.type = 0 AND m.is_visible = false
                AND m.label = '{_V2_MENU_DIR_LABEL}')
               OR (m.parent_id = v2_dir AND m.type = 2 AND m.is_visible = false
                   AND m.label <> '' AND m.perms IN ({_ALL_PERMS}))
           )
    ) THEN
        RAISE EXCEPTION 'v2_wp07a_permission_slug_conflict'
        USING ERRCODE='55000';
    END IF;
END $v2_wp07a_seed$
"""
    )


def upgrade() -> None:
    _seed_permissions()
    for name, table, columns in _KEYSET_INDEXES:
        op.execute(
            f"CREATE INDEX {name} ON trading.{table} {columns}"
        )


def downgrade() -> None:
    _assert_downgrade_safe()
    for name, _table, _columns in _KEYSET_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS trading.{name}")
    op.execute(
        f"""
DO $v2_wp07a_cleanup$
BEGIN
    IF to_regclass('public.menus') IS NOT NULL THEN
        DELETE FROM public.menus WHERE slug IN ({_ALL_SLUGS});
    END IF;
END $v2_wp07a_cleanup$
"""
    )
