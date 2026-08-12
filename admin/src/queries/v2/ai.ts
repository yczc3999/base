import { useQuery, type UseQueryOptions } from '@tanstack/vue-query'
import type { CursorPage, PageParams, AiInvocationRow } from '@/api/v2/types'
import { fetchAi } from '@/api/v2/ai'
import { v2QueryKeys } from './queryKeys'

export interface PageQueryInput {
  filters?: Record<string, unknown>
  cursor?: string | null
  asOf?: string | null
  limit?: number
  direction?: 'asc' | 'desc'
}

export function useAiPage(
  input: PageQueryInput,
  options?: Partial<UseQueryOptions<CursorPage<AiInvocationRow>>>,
) {
  const params: PageParams = {
    ...(input.filters ?? {}),
    cursor: input.cursor ?? undefined,
    limit: input.limit ?? 50,
    direction: input.direction ?? 'desc',
  }
  return useQuery({
    queryKey: v2QueryKeys.ai(input.filters ?? {}, input.cursor ?? null, input.asOf ?? null),
    queryFn: ({ signal }) => fetchAi(params, signal),
    placeholderData: (prev) => prev,  // 翻页保留上一页数据
    ...options,
  })
}
