import { computed, shallowRef, toValue, watch, type MaybeRefOrGetter } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { isRequestCanceled } from '@/api/request'
import { useUserStore } from '@/stores/user'
import type {
  CursorPage,
  OpaqueCursor,
  PageParams,
  SortDirection,
  UtcIsoString,
} from '@/api/v2/types'
import {
  cursorFilterIdentity,
  normalizeFilters,
  v2QueryKeys,
  type V2CursorQueryKey,
  type V2DetailQueryKey,
} from './queryKeys'

export type ReactiveValue<T> = MaybeRefOrGetter<T>

export interface PageQueryInput<F extends object = Record<string, never>> {
  filters?: ReactiveValue<F | undefined>
  cursor?: ReactiveValue<OpaqueCursor | null | undefined>
  asOf?: ReactiveValue<UtcIsoString | null | undefined>
  limit?: ReactiveValue<number | undefined>
  direction?: ReactiveValue<SortDirection | undefined>
}

export interface V2QueryOptions {
  enabled?: ReactiveValue<boolean>
  staleTime?: number
  gcTime?: number
  refetchOnWindowFocus?: boolean
}

export interface PageAnchor {
  identity: string
  cursor: OpaqueCursor | null
  asOf: UtcIsoString | null
}

const V2_DOMAIN_PERMISSIONS = Object.freeze({
  dashboard: 'v2:dashboard:view',
  markets: 'v2:markets:view',
  tags: 'v2:tags:view',
  components: 'v2:components:view',
  episodes: 'v2:episodes:view',
  decisions: 'v2:decisions:view',
  execution: 'v2:execution:view',
  models: 'v2:models:view',
  ai: 'v2:ai:view',
  costs: 'v2:costs:view',
  configuration: 'v2:config:view',
  releases: 'v2:release:view',
  evaluation: 'v2:evaluation:view',
  replay: 'v2:replay:view',
  integrity: 'v2:integrity:view',
  artifacts: 'v2:artifact:read',
} as const)

export function v2PermissionForDomain(domain: string): string | null {
  return V2_DOMAIN_PERMISSIONS[domain as keyof typeof V2_DOMAIN_PERMISSIONS] ?? null
}

export function isForbiddenV2Error(error: unknown): boolean {
  const candidate = error as {
    code?: number
    status?: number
    response?: { status?: number }
  } | null
  return candidate?.code === 403
    || candidate?.status === 403
    || candidate?.response?.status === 403
}

function useV2QueryAccess(domain: string, requested?: ReactiveValue<boolean>) {
  const userStore = useUserStore()
  const permission = v2PermissionForDomain(domain)
  // The guard loads user/menu permissions before entering authenticated routes. An empty
  // permission set for a normal user therefore means no V2 access, not implicit access.
  const deniedByPermission = computed(() => permission !== null
    && !userStore.isSuperAdmin
    && !userStore.permissions.includes(permission))
  const requestedEnabled = computed(() => requested === undefined || Boolean(toValue(requested)))
  const enabled = computed(() => requestedEnabled.value && !deniedByPermission.value)
  return { deniedByPermission, enabled }
}

export function createPageAnchor(
  identity: string,
  cursor: OpaqueCursor | null | undefined,
  asOf: UtcIsoString | null | undefined,
): PageAnchor {
  if (!cursor || !asOf) return { identity, cursor: null, asOf: null }
  return { identity, cursor, asOf }
}

/** A filter/direction transition invalidates cursor and asOf atomically. */
export function reconcilePageAnchor(
  current: PageAnchor,
  identity: string,
  requestedCursor: OpaqueCursor | null | undefined,
  requestedAsOf: UtcIsoString | null | undefined,
): PageAnchor {
  if (current.identity !== identity) return { identity, cursor: null, asOf: null }
  return createPageAnchor(identity, requestedCursor, requestedAsOf)
}

function sameValue(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right)
}

/**
 * Keep the previous page only for a cursor transition inside the same endpoint/filter/snapshot.
 * First-page keys carry asOf=null; the returned page's as_of binds the transition to page two.
 */
export function keepPreviousCursorPage<T>(
  previousData: CursorPage<T> | undefined,
  previousQuery: { queryKey: readonly unknown[] } | undefined,
  currentKey: V2CursorQueryKey,
): CursorPage<T> | undefined {
  if (!previousData || !previousQuery) return undefined
  const previousKey = previousQuery.queryKey
  if (previousKey.length !== currentKey.length) return undefined
  if (!sameValue(previousKey.slice(0, 6), currentKey.slice(0, 6))) return undefined

  const previousAsOf = previousKey[7]
  const currentAsOf = currentKey[7]
  const sameSnapshot = previousAsOf === currentAsOf
    || (previousAsOf === null && previousData.as_of === currentAsOf)
  return sameSnapshot ? previousData : undefined
}

/** TanStack's global retry=2 remains, except cancellation is terminal. */
export function shouldRetryV2Query(failureCount: number, error: unknown): boolean {
  return !isRequestCanceled(error) && failureCount < 2
}

export function useCursorPageQuery<T, F extends object>(
  domain: string,
  endpoint: ReactiveValue<string>,
  input: PageQueryInput<F>,
  fetchPage: (params: PageParams<F>, signal: AbortSignal) => Promise<CursorPage<T>>,
  options?: V2QueryOptions,
) {
  const access = useV2QueryAccess(domain, options?.enabled)
  const resolvedEndpoint = computed(() => toValue(endpoint))
  const normalizedFilters = computed(() => normalizeFilters(
    input.filters === undefined ? undefined : toValue(input.filters),
  ))
  const direction = computed<SortDirection>(() => (
    input.direction === undefined ? 'desc' : (toValue(input.direction) ?? 'desc')
  ))
  const limit = computed(() => (
    input.limit === undefined ? 50 : (toValue(input.limit) ?? 50)
  ))
  const requestedCursor = computed(() => (
    input.cursor === undefined ? null : (toValue(input.cursor) ?? null)
  ))
  const requestedAsOf = computed(() => (
    input.asOf === undefined ? null : (toValue(input.asOf) ?? null)
  ))

  const rawIdentity = computed(() => cursorFilterIdentity(v2QueryKeys.page(
    domain,
    resolvedEndpoint.value,
    normalizedFilters.value,
    direction.value,
    limit.value,
    null,
    null,
  )))
  const anchor = shallowRef(createPageAnchor(
    rawIdentity.value,
    requestedCursor.value,
    requestedAsOf.value,
  ))

  watch(
    [rawIdentity, requestedCursor, requestedAsOf],
    ([identity, cursor, asOf]) => {
      anchor.value = reconcilePageAnchor(anchor.value, identity, cursor, asOf)
    },
    { flush: 'sync' },
  )

  const queryKey = computed(() => v2QueryKeys.page(
    domain,
    resolvedEndpoint.value,
    normalizedFilters.value,
    direction.value,
    limit.value,
    anchor.value.cursor,
    anchor.value.asOf,
  ))
  const params = computed(() => ({
    ...normalizedFilters.value,
    cursor: anchor.value.cursor ?? undefined,
    limit: limit.value,
    direction: direction.value,
  }) as PageParams<F>)

  const query = useQuery({
    ...options,
    enabled: access.enabled,
    queryKey,
    queryFn: ({ signal }) => fetchPage(params.value, signal),
    placeholderData: (previousData, previousQuery) => keepPreviousCursorPage(
      previousData,
      previousQuery,
      queryKey.value,
    ),
    retry: shouldRetryV2Query,
  })

  return Object.assign(query, {
    effectiveCursor: computed(() => anchor.value.cursor),
    effectiveAsOf: computed(() => anchor.value.asOf),
    filterIdentity: rawIdentity,
    denied: computed(() => access.deniedByPermission.value || isForbiddenV2Error(query.error.value)),
    displayError: computed(() => query.isError.value && !isForbiddenV2Error(query.error.value)
      ? String(query.error.value)
      : null),
  })
}

export function useDetailQuery<T>(
  domain: string,
  endpoint: string,
  identity: readonly ReactiveValue<string | null>[],
  fetchDetail: (signal: AbortSignal) => Promise<T>,
  options?: V2QueryOptions,
) {
  const access = useV2QueryAccess(domain, options?.enabled)
  const resolvedIdentity = computed(() => identity.map((part) => toValue(part)))
  const queryKey = computed<V2DetailQueryKey>(() => v2QueryKeys.detail(
    domain,
    endpoint,
    ...resolvedIdentity.value,
  ))
  const query = useQuery({
    ...options,
    enabled: access.enabled,
    queryKey,
    queryFn: ({ signal }) => fetchDetail(signal),
    retry: shouldRetryV2Query,
  })
  return Object.assign(query, {
    denied: computed(() => access.deniedByPermission.value || isForbiddenV2Error(query.error.value)),
    displayError: computed(() => query.isError.value && !isForbiddenV2Error(query.error.value)
      ? String(query.error.value)
      : null),
  })
}
