import type {
  OpaqueCursor,
  SortDirection,
  UtcIsoString,
} from '@/api/v2/types'

const SCOPE = 'v2-admin-read' as const

export type NormalizedFilters = Readonly<Record<string, string>>

export type V2CursorQueryKey = readonly [
  typeof SCOPE,
  domain: string,
  endpoint: string,
  filters: NormalizedFilters,
  direction: SortDirection,
  limit: number,
  cursor: OpaqueCursor | null,
  asOf: UtcIsoString | null,
]

export type V2DetailQueryKey = readonly [
  typeof SCOPE,
  domain: string,
  endpoint: string,
  ...identity: readonly (string | null)[],
]

/** Canonicalize exactly what Axios puts on the wire: sorted keys and string values. */
export function normalizeFilters(filters: object | undefined): NormalizedFilters {
  const entries = Object.entries(filters ?? {})
    .filter((entry): entry is [string, string | number | boolean] => entry[1] !== undefined)
    .map(([key, value]) => {
      if (typeof value !== 'string' && typeof value !== 'number' && typeof value !== 'boolean') {
        throw new TypeError(`unsupported_filter_value:${key}`)
      }
      return [key, String(value)] as const
    })
    .sort(([left], [right]) => left.localeCompare(right))
  return Object.freeze(Object.fromEntries(entries))
}

export const v2QueryKeys = {
  page(
    domain: string,
    endpoint: string,
    filters: object | undefined,
    direction: SortDirection,
    limit: number,
    cursor: OpaqueCursor | null,
    asOf: UtcIsoString | null,
  ): V2CursorQueryKey {
    return [SCOPE, domain, endpoint, normalizeFilters(filters), direction, limit, cursor, asOf]
  },

  detail(
    domain: string,
    endpoint: string,
    ...identity: readonly (string | null)[]
  ): V2DetailQueryKey {
    return [SCOPE, domain, endpoint, ...identity]
  },
}

/** Cursor identity excludes limit, but includes endpoint/filter/direction. */
export function cursorFilterIdentity(key: V2CursorQueryKey): string {
  return JSON.stringify([key[0], key[1], key[2], key[3], key[4]])
}
