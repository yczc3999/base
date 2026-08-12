/** V2 Admin query keys（WP-07A Checkpoint C）。query key 至少含 domain/endpoint/
normalized filters/cursor/as_of；filter 改变时调用方须清空 cursor/as_of。 */

export const v2QueryKeys = {
  dashboard: (filters: Record<string, unknown>, cursor: string | null, asOf: string | null) => ['v2', 'dashboard', filters, cursor, asOf] as const,
  markets: (filters: Record<string, unknown>, cursor: string | null, asOf: string | null) => ['v2', 'markets', filters, cursor, asOf] as const,
  components: (filters: Record<string, unknown>, cursor: string | null, asOf: string | null) => ['v2', 'components', filters, cursor, asOf] as const,
  episodes: (filters: Record<string, unknown>, cursor: string | null, asOf: string | null) => ['v2', 'episodes', filters, cursor, asOf] as const,
  decisions: (filters: Record<string, unknown>, cursor: string | null, asOf: string | null) => ['v2', 'decisions', filters, cursor, asOf] as const,
  execution: (filters: Record<string, unknown>, cursor: string | null, asOf: string | null) => ['v2', 'execution', filters, cursor, asOf] as const,
  models: (filters: Record<string, unknown>, cursor: string | null, asOf: string | null) => ['v2', 'models', filters, cursor, asOf] as const,
  ai: (filters: Record<string, unknown>, cursor: string | null, asOf: string | null) => ['v2', 'ai', filters, cursor, asOf] as const,
  costs: (filters: Record<string, unknown>, cursor: string | null, asOf: string | null) => ['v2', 'costs', filters, cursor, asOf] as const,
  configuration: (filters: Record<string, unknown>, cursor: string | null, asOf: string | null) => ['v2', 'configuration', filters, cursor, asOf] as const,
  evaluation: (filters: Record<string, unknown>, cursor: string | null, asOf: string | null) => ['v2', 'evaluation', filters, cursor, asOf] as const,
  replay: (filters: Record<string, unknown>, cursor: string | null, asOf: string | null) => ['v2', 'replay', filters, cursor, asOf] as const,
  integrity: (filters: Record<string, unknown>, cursor: string | null, asOf: string | null) => ['v2', 'integrity', filters, cursor, asOf] as const,
}

