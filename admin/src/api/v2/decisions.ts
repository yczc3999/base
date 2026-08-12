import { requestV2 } from '../request'
import type { CursorPage, PageParams, DecisionRow } from './types'

export async function fetchDecisions(
  params: PageParams,
  signal?: AbortSignal,
): Promise<CursorPage<DecisionRow>> {
  return requestV2<CursorPage<DecisionRow>>({ url: '/admin/v2/decisions', params, signal })
}

export async function fetchDecision(id: string, signal?: AbortSignal): Promise<unknown> {
  return requestV2<unknown>({ url: '/admin/v2/decisions/{id}'.replace('{id}', id), signal })
}
