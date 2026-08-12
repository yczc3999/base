import { requestV2 } from '../request'
import type { CursorPage, PageParams, CostRow } from './types'

export async function fetchCosts(
  params: PageParams,
  signal?: AbortSignal,
): Promise<CursorPage<CostRow>> {
  return requestV2<CursorPage<CostRow>>({ url: '/admin/v2/costs', params, signal })
}
