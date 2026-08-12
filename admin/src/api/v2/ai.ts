import { requestV2 } from '../request'
import type { CursorPage, PageParams, AiInvocationRow } from './types'

export async function fetchAi(
  params: PageParams,
  signal?: AbortSignal,
): Promise<CursorPage<AiInvocationRow>> {
  return requestV2<CursorPage<AiInvocationRow>>({ url: '/admin/v2/ai-invocations', params, signal })
}
