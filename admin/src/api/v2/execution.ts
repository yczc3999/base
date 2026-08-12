import { requestV2 } from '../request'
import { pathSegment } from './path'
import type {
  CursorPage,
  ExecutionTraceResponse,
  IntentRow,
  LedgerFilters,
  LedgerRow,
  OrderRow,
  PageParams,
  PositionRow,
  StatusFilters,
} from './types'

export function fetchIntents(
  params: PageParams<StatusFilters>,
  signal?: AbortSignal,
): Promise<CursorPage<IntentRow>> {
  return requestV2({ url: '/admin/v2/execution/intents', params, signal })
}

export function fetchOrders(
  params: PageParams<StatusFilters>,
  signal?: AbortSignal,
): Promise<CursorPage<OrderRow>> {
  return requestV2({ url: '/admin/v2/execution/orders', params, signal })
}

export function fetchPositions(
  params: PageParams,
  signal?: AbortSignal,
): Promise<CursorPage<PositionRow>> {
  return requestV2({ url: '/admin/v2/execution/positions', params, signal })
}

export function fetchLedger(
  params: PageParams<LedgerFilters>,
  signal?: AbortSignal,
): Promise<CursorPage<LedgerRow>> {
  return requestV2({ url: '/admin/v2/execution/ledger', params, signal })
}

export function fetchExecutionTrace(
  decisionId: string,
  signal?: AbortSignal,
): Promise<ExecutionTraceResponse> {
  return requestV2({
    url: `/admin/v2/execution/${pathSegment(decisionId)}/trace`,
    signal,
  })
}
