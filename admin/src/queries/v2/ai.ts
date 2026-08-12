import { toValue } from 'vue'
import { fetchAi, fetchAiDetail } from '@/api/v2/ai'
import type { AiFilters, UtcIsoString } from '@/api/v2/types'
import {
  useCursorPageQuery,
  useDetailQuery,
  type PageQueryInput,
  type ReactiveValue,
  type V2QueryOptions,
} from './page'

export function useAiPage(input: PageQueryInput<AiFilters>, options?: V2QueryOptions) {
  return useCursorPageQuery('ai', 'invocations', input, fetchAi, options)
}

export function useAiDetail(
  id: ReactiveValue<string>,
  occurredAt: ReactiveValue<UtcIsoString>,
  options?: V2QueryOptions,
) {
  return useDetailQuery(
    'ai',
    'invocation-detail',
    [occurredAt, id],
    (signal) => fetchAiDetail(toValue(id), toValue(occurredAt), signal),
    options,
  )
}
