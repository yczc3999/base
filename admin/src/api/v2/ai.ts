import { requestV2 } from '../request'
import { pathSegment } from './path'
import type { AiDetail, AiFilters, AiInvocationRow, CursorPage, PageParams, UtcIsoString } from './types'

export async function fetchAi(
  params: PageParams<AiFilters>,
  signal?: AbortSignal,
): Promise<CursorPage<AiInvocationRow>> {
  return requestV2<CursorPage<AiInvocationRow>>({ url: '/admin/v2/ai-invocations', params, signal })
}

/** AI invocation identity is the partition key `(occurred_at, id)`. */
export async function fetchAiDetail(
  id: string,
  occurredAt: UtcIsoString,
  signal?: AbortSignal,
): Promise<AiDetail> {
  return requestV2<AiDetail>({
    url: `/admin/v2/ai-invocations/${pathSegment(id)}`,
    params: { occurred_at: occurredAt },
    signal,
  })
}
