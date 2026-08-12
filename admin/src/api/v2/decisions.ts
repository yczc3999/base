import { requestV2 } from '../request'
import { pathSegment } from './path'
import type { CursorPage, DecisionDetail, DecisionFilters, DecisionRow, PageParams } from './types'

export async function fetchDecisions(
  params: PageParams<DecisionFilters>,
  signal?: AbortSignal,
): Promise<CursorPage<DecisionRow>> {
  return requestV2<CursorPage<DecisionRow>>({ url: '/admin/v2/decisions', params, signal })
}

export async function fetchDecision(id: string, signal?: AbortSignal): Promise<DecisionDetail> {
  return requestV2<DecisionDetail>({ url: `/admin/v2/decisions/${pathSegment(id)}`, signal })
}
