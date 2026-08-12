import { useQuery, type UseQueryOptions } from '@tanstack/vue-query'
import type { CursorPage, PageParams, IntegrityRow } from '@/api/v2/types'
import { fetchIntegrity } from '@/api/v2/integrity'
import { v2QueryKeys } from './queryKeys'

export interface PageQueryInput {
  filters?: Record<string, unknown>
  cursor?: string | null
  asOf?: string | null
  limit?: number
  direction?: 'asc' | 'desc'
}

export function useIntegrityPage(
  input: PageQueryInput,
  options?: Partial<UseQueryOptions<CursorPage<IntegrityRow>>>,
) {
  const params: PageParams = {
    ...(input.filters ?? {}),
    cursor: input.cursor ?? undefined,
    limit: input.limit ?? 50,
    direction: input.direction ?? 'desc',
  }
  return useQuery({
    queryKey: v2QueryKeys.integrity(input.filters ?? {}, input.cursor ?? null, input.asOf ?? null),
    queryFn: ({ signal }) => fetchIntegrity(params, signal),
    placeholderData: (prev) => prev,  // 翻页保留上一页数据
    ...options,
  })
}
