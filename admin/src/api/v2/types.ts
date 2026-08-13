/** V2 Admin Read API typed contracts (WP-07A Checkpoint C). */

/** PostgreSQL BIGINT identity serialized as a base-10 JSON string. */
export type EntityId = string

/** Any non-identity BIGINT serialized as a base-10 JSON string. */
export type Int64String = string

/** PostgreSQL NUMERIC serialized as a decimal JSON string. */
export type DecimalString = string

export type UtcIsoString = string
export type Sha256 = string
export type OpaqueCursor = string

export type JsonPrimitive = string | number | boolean | null
export type JsonValue = JsonPrimitive | JsonObject | readonly JsonValue[]
export interface JsonObject { readonly [key: string]: JsonValue }

export interface CursorPage<T> {
  items: T[]
  next_cursor: OpaqueCursor | null
  has_more: boolean
  as_of: UtcIsoString
  filter_hash: Sha256
}

export interface Authoritative {
  authoritative: true
  as_of: UtcIsoString
}

export type SortDirection = 'asc' | 'desc'
export type FilterValue = string | boolean | undefined
export type FilterRecord = Readonly<Record<string, FilterValue>>

export type PageParams<F extends object = Record<string, never>> = F & {
  cursor?: OpaqueCursor
  limit?: number
  direction?: SortDirection
}

export interface MarketFilters {
  neg_risk?: boolean | string
  closed?: boolean | string
}

export interface TagFilters {
  slug?: string
  seen_in_catalog?: boolean | string
  disposition?: string
}

export interface EpisodeFilters { status?: string }
export interface DecisionFilters { status?: string; decision_class?: string }
export interface StatusFilters { status?: string }
export interface AiFilters { role?: string; lifecycle_state?: string }
export interface CostFilters { cost_kind?: string }
export interface LabelFilters { state?: string }
export interface LedgerFilters { kind?: string }
export interface AlertFilters { severity?: string }

export interface MarketRow {
  id: EntityId
  gamma_market_id: string
  gamma_event_id: string | null
  condition_id: string | null
  question: string | null
  slug: string | null
  ticker: string | null
  active: boolean
  closed: boolean
  accepting_orders: boolean
  neg_risk: boolean
  volume: DecimalString
  liquidity: DecimalString
  start_date: UtcIsoString | null
  end_date: UtcIsoString | null
  closed_at: UtcIsoString | null
  created_at: UtcIsoString | null
}

export type TagDisposition = 'SELECT' | 'DEFER' | 'REJECT'

export interface TagRow {
  id: EntityId
  gamma_tag_id: string
  slug: string | null
  label: string | null
  seen_in_catalog: boolean
  seen_in_event: boolean
  disposition: TagDisposition | null
  event_count: string
  observed_at: UtcIsoString | null
  created_at: UtcIsoString | null
}

export interface TagSyncResult {
  ok: boolean
  upserted: number
  pages: number
  truncated: boolean
}

export interface ComponentRow {
  id: EntityId
  component_key: string
  cost_budget: DecimalString | null
  description: string | null
  created_at: UtcIsoString | null
}

export interface EpisodeRow {
  id: EntityId
  episode_key: Sha256
  decision_opportunity_id: EntityId | null
  component_version_id: EntityId | null
  strategy_version_id: EntityId | null
  trigger: string
  status: string
  cognition_status: string
  horizon: string | null
  cutoff_at: UtcIsoString | null
  prior_frozen_at: UtcIsoString | null
  forecast_committed_at: UtcIsoString | null
  created_at: UtcIsoString
}

export interface DecisionRow {
  id: EntityId
  decision_key: Sha256
  episode_id: EntityId | null
  forecast_submission_id: EntityId | null
  strategy_version_id: EntityId | null
  release_manifest_id: EntityId | null
  decision_class: string
  status: string
  selected_action_type: string | null
  trigger_at: UtcIsoString | null
  decided_at: UtcIsoString | null
  input_hash: Sha256 | null
  output_hash: Sha256 | null
  reason_code: string | null
  created_at: UtcIsoString
}

export interface IntentRow {
  id: EntityId
  intent_key: string
  intent_hash: Sha256
  trade_decision_id: EntityId
  action_set_id: EntityId
  status: string
  ttl_at: UtcIsoString | null
  created_at: UtcIsoString
}

export interface OrderRow {
  id: EntityId
  order_key: string
  external_order_id: string | null
  token_id: string
  side: string
  price: DecimalString
  size: DecimalString
  filled_size: DecimalString
  status: string
  account_id: EntityId
  created_at: UtcIsoString
}

export interface PositionRow {
  id: EntityId
  portfolio_namespace: string
  contract_spec_id: EntityId
  token_id: string
  market_id: EntityId
  quantity: DecimalString
  cost_basis: DecimalString
  account_id: EntityId
  updated_at: UtcIsoString
}

export interface LedgerRow {
  id: EntityId
  transaction_key: string
  status: string
  kind: string
  trade_decision_id: EntityId | null
  execution_id: EntityId | null
  chain_operation_id: EntityId | null
  portfolio_namespace: string
  posted_at: UtcIsoString | null
  created_at: UtcIsoString
}

export interface ExecutionTraceRow {
  kind: 'intent' | 'execution' | 'envelope' | 'order' | 'ledger' | 'chain_operation'
  id: EntityId
  ref_key: string
  state: string
  ts: UtcIsoString
}

export interface ExecutionTraceResponse { items: ExecutionTraceRow[] }

export interface ModelRouteRow {
  id: EntityId
  strategy_version_id: EntityId | null
  role: string
  provider: string | null
  route: string | null
  model_ref: string | null
  network_policy: string
  binding_version: number | null
  content_hash: Sha256
  created_at: UtcIsoString | null
}

export interface AiInvocationRow {
  id: EntityId
  occurred_at: UtcIsoString
  invocation_key: Sha256
  episode_id: EntityId
  stage: string
  role: string
  attempt_no: number
  requested_provider: string | null
  requested_route: string | null
  requested_model: string | null
  returned_provider: string | null
  returned_route: string | null
  returned_model: string | null
  lifecycle_state: string
  terminal_reason: string | null
  retriable: boolean
  input_tokens: Int64String | null
  output_tokens: Int64String | null
  cost_estimated: DecimalString | null
  created_at: UtcIsoString
}

export interface AiInvocationDetail extends AiInvocationRow {
  experiment_variant: string | null
  parent_invocation_id: EntityId | null
  retry_of_invocation_id: EntityId | null
  fallback_of_invocation_id: EntityId | null
  causation_event_id: EntityId | null
  release_manifest_id: EntityId | null
  strategy_version_id: EntityId | null
  config_version_id: EntityId | null
  model_role_binding_id: EntityId | null
  cache_key_hash: Sha256 | null
  network_policy: string
  context_class: string
  prompt_version: string | null
  prompt_artifact_ref: Sha256 | null
  schema_version: string | null
  schema_artifact_ref: Sha256 | null
  request_artifact_ref: Sha256 | null
  raw_response_artifact_ref: Sha256 | null
  parsed_output_artifact_ref: Sha256 | null
  normalized_output_artifact_ref: Sha256 | null
  cache_tokens: Int64String | null
  reasoning_tokens: Int64String | null
  tool_count: number
  search_count: number
  cost_currency: string | null
  accepted_at: UtcIsoString | null
  queued_at: UtcIsoString | null
  started_at: UtcIsoString | null
  first_token_at: UtcIsoString | null
  response_at: UtcIsoString | null
  parsed_at: UtcIsoString | null
  validated_at: UtcIsoString | null
  completed_at: UtcIsoString | null
}

export interface AiToolCallRow {
  id: EntityId
  tool_type: string
  tool_version: string
  status: string
  error_code: string | null
  source_urls: readonly string[] | null
  result_artifact_ref: Sha256 | null
  cost: DecimalString | null
  occurred_at: UtcIsoString
}

export interface AiValidationRow {
  id: EntityId
  validator_name: string
  validator_version: string
  passed: boolean
  severity: string
  reason_code: string | null
  details_artifact_hash: Sha256 | null
  occurred_at: UtcIsoString
}

export interface AiDownstreamRow {
  id: EntityId
  retry_of: EntityId | null
  fallback_of: EntityId | null
  lifecycle_state: string
  occurred_at: UtcIsoString
}

export interface AiDetail {
  invocation: AiInvocationDetail
  model_role_binding: ModelRouteRow | null
  tool_calls: AiToolCallRow[]
  validations: AiValidationRow[]
  downstream: AiDownstreamRow[]
}

export interface CostRow {
  id: EntityId
  cost_key: string
  cost_kind: string
  amount: DecimalString
  release_manifest_id: EntityId | null
  episode_id: EntityId | null
  period_start: UtcIsoString | null
  period_end: UtcIsoString | null
  created_at: UtcIsoString | null
}

export interface ConfigRow {
  id: EntityId
  config_key: string
  version_no: number
  schema_version: number
  content_hash: Sha256
  status: string
  creator: string | null
  created_at: UtcIsoString | null
}

export interface ConfigDetail extends ConfigRow { content: JsonValue }

export interface ReleaseRow {
  id: EntityId
  release_name: string
  git_sha: string
  image_digest: string
  db_revision: string
  total_hash: Sha256
  status: string
  creator: string | null
  created_at: UtcIsoString
}

export interface ReleaseDetailRow extends ReleaseRow {
  config_version_id: EntityId
  strategy_version_id: EntityId
  execution_spec_version_id: EntityId
  capital_permission_manifest_id: EntityId
}

export interface ReleasePartRow {
  kind: 'config' | 'strategy' | 'execution_spec' | 'capital_permission'
  id: EntityId
  ref: string
  version_no: number
  content_hash: Sha256
  status: string
}

export interface ReleaseDetail { release: ReleaseDetailRow; exact_parts: ReleasePartRow[] }

export interface ResolutionLabelRow {
  id: EntityId
  contract_spec_id: EntityId
  label_key: string
  version_no: number
  state: string
  resolution_state: string
  policy_code_hash: Sha256
  supersedes_id: EntityId | null
  created_at: UtcIsoString
}

export interface MetricRunRow {
  id: EntityId
  run_key: string
  split: string
  status: string
  n_market: number
  n_episode: number
  n_eff: DecimalString
  artifact_hash: Sha256
  release_manifest_id: EntityId
  created_at: UtcIsoString
}

export interface PromotionRow {
  id: EntityId
  promotion_key: string
  metric_run_id: EntityId
  promotion_type: string
  from_ref: string
  to_ref: string
  evidence_manifest_hash: Sha256
  status: string
  reason_code: string | null
  future_effective_at: UtcIsoString | null
  created_at: UtcIsoString
}

export interface ReplayRow {
  id: EntityId
  run_key: string
  replay_kind: string
  manifest_hash: Sha256
  code_hash: Sha256
  seed: Int64String
  input_artifact_hash: Sha256
  output_artifact_hash: Sha256
  created_at: UtcIsoString | null
}

export interface AlertRow {
  id: EntityId
  alert_key: string
  severity: string | null
  code: string | null
  message_redacted: string | null
  created_at: UtcIsoString | null
}

export type WorkflowAggregateType = 'episode' | 'decision' | 'intent' | 'chain_operation'
  | 'forecast_submission' | 'evidence_bundle'

export interface WorkflowRow {
  id: EntityId
  event_key: string
  event_type: string
  aggregate_type: WorkflowAggregateType
  aggregate_id: string
  payload_hash: Sha256
  created_at: UtcIsoString
}

export interface RuntimeSnapshot extends JsonObject {
  readonly status: string
}

export interface IntegrityChain {
  workflows: JsonObject[]
  outbox: JsonObject[]
  external_calls: JsonObject[]
  alerts: AlertRow[]
}

export interface TimelineRow {
  kind: 'submission' | 'gate' | 'info_snapshot'
  id: EntityId
  created_at: UtcIsoString
  state: string
}

export interface MarketDetail {
  market: MarketRow & { content_hash: Sha256; raw_artifact_ref: Sha256 | null }
  snapshot: JsonObject | null
  specs: JsonObject[]
  current: JsonObject | null
  cohort: JsonObject[]
}

export interface ComponentDetail {
  component: ComponentRow
  versions: JsonObject[]
  member_contracts: JsonObject[]
}

export interface EpisodeDetail {
  episode: EpisodeRow & {
    objective_contract_id: EntityId | null
    drop_reason: string | null
    evidence_bundle_at: UtcIsoString | null
  }
  priors: JsonObject[]
  evidence_bundles: JsonObject[]
  submissions: JsonObject[]
  gates: JsonObject[]
}

export interface DecisionDetail {
  decision: DecisionRow & {
    forecast_lease_id: EntityId | null
    capital_permission_manifest_id: EntityId | null
    quote_bound_at: UtcIsoString | null
  }
  quote_bindings: JsonObject[]
  underwriting_plans: JsonObject[]
  action_sets: JsonObject[]
  intents: JsonObject[]
}

export type ProjectionName = 'ops_health_current' | 'pipeline_funnel_hourly'
  | 'account_risk_current' | 'provider_cost_daily' | 'latest_chain_summary'

export interface DashboardBlock {
  rows: JsonObject[]
  as_of: UtcIsoString
  source_high_watermark: Int64String | null
  projection_version: number
  projection_hash: Sha256
  freshness_status: 'fresh' | 'stale' | 'missing'
}

export interface DashboardResponse {
  blocks: Record<ProjectionName, DashboardBlock>
  authoritative: Record<string, Authoritative>
  as_of: UtcIsoString
}

export interface ArtifactLineageRow {
  id: EntityId
  from_artifact_id: EntityId
  to_artifact_id: EntityId
  relation: string
  invocation_ref: string | null
  created_at: UtcIsoString
}

export interface ArtifactMetadata {
  content_hash: Sha256
  content_type: string
  content_length: DecimalString
  lineage: ArtifactLineageRow[]
  stored_at: UtcIsoString | null
}

export interface ArtifactByteRange { start: Int64String; end: Int64String }

export interface ArtifactContent {
  data: ArrayBuffer
  content_type: string
  content_range: string
  accept_ranges: string
  etag: string
}
