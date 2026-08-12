/** V2 Admin Read API typed contracts（WP-07A Checkpoint C）。 */

/** BIGINT id —— 十进制字符串，禁止 JS number */
export type EntityId = string

/** NUMERIC 金额/数量 —— decimal string，禁止 float */
export type DecimalString = string

/** UTC ISO-8601 时间 */
export type UtcIsoString = string

/** 64 位小写十六进制 hash */
export type Sha256 = string

/** HMAC opaque keyset cursor —— 浏览器不解码、不重签 */
export type OpaqueCursor = string

/** 列表统一响应 envelope（无 page/pageSize/total） */
export interface CursorPage<T> {
  items: T[]
  next_cursor: OpaqueCursor | null
  has_more: boolean
  as_of: UtcIsoString
  filter_hash: Sha256
}

/** 权威事实标记：金额/数量 decimal string + 明确 read snapshot */
export interface Authoritative {
  authoritative: true
  as_of: UtcIsoString
}

/** 分页请求参数（limit 范围 1–200，默认 50） */
export interface PageParams {
  cursor?: OpaqueCursor
  limit?: number
  direction?: 'asc' | 'desc'
  [filter: string]: string | number | boolean | undefined
}

/** market 摘要行 */
export interface MarketRow {
  id: EntityId
  gamma_market_id: string
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
  created_at: UtcIsoString | null
}

/** episode 摘要行 */
export interface EpisodeRow {
  id: EntityId
  episode_key: Sha256
  decision_opportunity_id: EntityId | null
  status: string
  cognition_status: string
  cutoff_at: UtcIsoString | null
  created_at: UtcIsoString
}

/** ai invocation 摘要行（不内联 raw prompt/response） */
export interface AiInvocationRow {
  id: EntityId
  occurred_at: UtcIsoString
  invocation_key: Sha256
  role: string
  lifecycle_state: string
  requested_provider: string | null
  returned_model: string | null
  input_tokens: number | null
  output_tokens: number | null
  cost_estimated: DecimalString | null
  created_at: UtcIsoString
}

/** component 摘要行 */
export interface ComponentRow {
  id: EntityId
  component_key: string
  cost_budget: DecimalString | null
  description: string | null
  created_at: UtcIsoString | null
}

/** decision 摘要行 */
export interface DecisionRow {
  id: EntityId
  decision_key: Sha256
  episode_id: EntityId | null
  strategy_version_id: EntityId | null
  decision_class: string
  status: string
  selected_action_type: string | null
  trigger_at: UtcIsoString | null
  decided_at: UtcIsoString | null
  input_hash: Sha256 | null
  created_at: UtcIsoString
}

/** execution 摘要行（intents/orders/positions/ledger 通用摘要） */
export interface ExecutionRow {
  id: EntityId
  kind: string
  status: string
  ref_key: string
  created_at: UtcIsoString | null
  [key: string]: string | number | boolean | null | undefined
}

/** model route 摘要行 */
export interface ModelRouteRow {
  id: EntityId
  strategy_version_id: EntityId | null
  role: string
  provider: string | null
  route: string | null
  model_ref: string | null
  binding_version: number | null
  content_hash: Sha256
}

/** cost 摘要行 */
export interface CostRow {
  id: EntityId
  cost_key: string
  cost_kind: string
  amount: DecimalString
  release_manifest_id: EntityId | null
  period_start: UtcIsoString | null
  created_at: UtcIsoString | null
}

/** strategy config 摘要行（只读；不实现 draft/publish） */
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

/** evaluation 摘要行（labels/metrics/promotions） */
export interface EvaluationRow {
  id: EntityId
  ref_key: string
  state: string
  status: string
  created_at: UtcIsoString | null
  [key: string]: string | number | boolean | null | undefined
}

/** replay 摘要行 */
export interface ReplayRow {
  id: EntityId
  run_key: string
  replay_kind: string
  manifest_hash: Sha256
  code_hash: Sha256
  seed: number
  input_artifact_hash: Sha256
  output_artifact_hash: Sha256
  created_at: UtcIsoString | null
}

/** integrity 摘要行（alerts/workflows/external calls） */
export interface IntegrityRow {
  id: EntityId
  ref_key: string
  severity: string | null
  code: string | null
  message_redacted: string | null
  created_at: UtcIsoString | null
  [key: string]: string | number | boolean | null | undefined
}

/** dashboard 响应（只读五张 projection + authoritative 摘要） */
export interface DashboardBlock {
  rows: unknown[]
  freshness_status: 'fresh' | 'stale' | 'missing'
  as_of: UtcIsoString
}

export interface DashboardResponse {
  blocks: Record<string, DashboardBlock>
  as_of: UtcIsoString
}
