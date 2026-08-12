import { requestV2 } from '../request'
import { pathSegment } from './path'
import type {
  AlertFilters,
  AlertRow,
  CursorPage,
  IntegrityChain,
  PageParams,
  RuntimeSnapshot,
  WorkflowAggregateType,
} from './types'

export function fetchIntegrityRuntime(signal?: AbortSignal): Promise<RuntimeSnapshot> {
  return requestV2({ url: '/admin/v2/integrity/runtime', signal })
}

export function fetchAlerts(
  params: PageParams<AlertFilters>,
  signal?: AbortSignal,
): Promise<CursorPage<AlertRow>> {
  return requestV2({ url: '/admin/v2/integrity/alerts', params, signal })
}

export function fetchIntegrityWorkflow(
  aggregateType: WorkflowAggregateType,
  aggregateId: string,
  signal?: AbortSignal,
): Promise<IntegrityChain> {
  return requestV2({
    url: `/admin/v2/integrity/workflows/${pathSegment(aggregateType)}/${pathSegment(aggregateId)}`,
    signal,
  })
}
