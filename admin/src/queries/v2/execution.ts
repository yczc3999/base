import { toValue } from 'vue'
import {
  fetchExecutionTrace,
  fetchIntents,
  fetchLedger,
  fetchOrders,
  fetchPositions,
} from '@/api/v2/execution'
import type { LedgerFilters, StatusFilters } from '@/api/v2/types'
import {
  useCursorPageQuery,
  useDetailQuery,
  type PageQueryInput,
  type ReactiveValue,
  type V2QueryOptions,
} from './page'

export function useIntentsPage(input: PageQueryInput<StatusFilters>, options?: V2QueryOptions) {
  return useCursorPageQuery('execution', 'intents', input, fetchIntents, options)
}

export function useOrdersPage(input: PageQueryInput<StatusFilters>, options?: V2QueryOptions) {
  return useCursorPageQuery('execution', 'orders', input, fetchOrders, options)
}

export function usePositionsPage(input: PageQueryInput, options?: V2QueryOptions) {
  return useCursorPageQuery('execution', 'positions', input, fetchPositions, options)
}

export function useLedgerPage(input: PageQueryInput<LedgerFilters>, options?: V2QueryOptions) {
  return useCursorPageQuery('execution', 'ledger', input, fetchLedger, options)
}

export function useExecutionTrace(
  decisionId: ReactiveValue<string>,
  options?: V2QueryOptions,
) {
  return useDetailQuery(
    'execution',
    'trace',
    [decisionId],
    (signal) => fetchExecutionTrace(toValue(decisionId), signal),
    options,
  )
}

/** Compatibility alias now points at the real intents endpoint. */
export const useExecutionPage = useIntentsPage
