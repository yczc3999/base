import { useQuery, type UseQueryOptions } from '@tanstack/vue-query'
import type { CursorPage, PageParams, ModelRouteRow } from '@/api/v2/types'
import { fetchModels } from '@/api/v2/models'
import { v2QueryKeys } from './queryKeys'

export interface PageQueryInput {
  filters?: Record<string, unknown>
  cursor?: string | null
  asOf?: string | null
  limit?: number
  direction?: 'asc' | 'desc'
}

export function useModelsPage(
  input: PageQueryInput,
  options?: Partial<UseQueryOptions<CursorPage<ModelRouteRow>>>,
) {
  const params: PageParams = {
    ...(input.filters ?? {}),
    cursor: input.cursor ?? undefined,
    limit: input.limit ?? 50,
    direction: input.direction ?? 'desc',
  }
  return useQuery({
    queryKey: v2QueryKeys.models(input.filters ?? {}, input.cursor ?? null, input.asOf ?? null),
    queryFn: ({ signal }) => fetchModels(params, signal),
    placeholderData: (prev) => prev,  // 翻页保留上一页数据
    ...options,
  })
}
