import { toValue } from 'vue'
import { fetchDecision, fetchDecisions } from '@/api/v2/decisions'
import type { DecisionFilters } from '@/api/v2/types'
import {
  useCursorPageQuery,
  useDetailQuery,
  type PageQueryInput,
  type ReactiveValue,
  type V2QueryOptions,
} from './page'

export function useDecisionsPage(
  input: PageQueryInput<DecisionFilters>,
  options?: V2QueryOptions,
) {
  return useCursorPageQuery('decisions', 'list', input, fetchDecisions, options)
}

export function useDecision(id: ReactiveValue<string>, options?: V2QueryOptions) {
  return useDetailQuery(
    'decisions',
    'detail',
    [id],
    (signal) => fetchDecision(toValue(id), signal),
    options,
  )
}
