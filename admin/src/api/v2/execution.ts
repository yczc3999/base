import { requestV2 } from '../request'
import type { CursorPage, PageParams, ExecutionRow } from './types'

export async function fetchExecution(
  params: PageParams,
  signal?: AbortSignal,
): Promise<CursorPage<ExecutionRow>> {
  return requestV2<CursorPage<ExecutionRow>>({ url: '/admin/v2/execution', params, signal })
}
