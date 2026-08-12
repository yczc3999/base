import { toValue } from 'vue'
import { fetchAlerts, fetchIntegrityRuntime, fetchIntegrityWorkflow } from '@/api/v2/integrity'
import type { AlertFilters, WorkflowAggregateType } from '@/api/v2/types'
import {
  useCursorPageQuery,
  useDetailQuery,
  type PageQueryInput,
  type ReactiveValue,
  type V2QueryOptions,
} from './page'

export function useIntegrityRuntime(options?: V2QueryOptions) {
  return useDetailQuery('integrity', 'runtime', [], fetchIntegrityRuntime, options)
}

export function useAlertsPage(input: PageQueryInput<AlertFilters>, options?: V2QueryOptions) {
  return useCursorPageQuery('integrity', 'alerts', input, fetchAlerts, options)
}

export function useIntegrityWorkflow(
  aggregateType: ReactiveValue<WorkflowAggregateType>,
  aggregateId: ReactiveValue<string>,
  options?: V2QueryOptions,
) {
  return useDetailQuery(
    'integrity',
    'workflow',
    [aggregateType, aggregateId],
    (signal) => fetchIntegrityWorkflow(toValue(aggregateType), toValue(aggregateId), signal),
    options,
  )
}

/** Compatibility alias now points at the real alerts endpoint. */
export const useIntegrityPage = useAlertsPage
