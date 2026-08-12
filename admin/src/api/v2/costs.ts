import { requestV2 } from '../request'
import type { CostFilters, CostRow, CursorPage, PageParams } from './types'

export async function fetchCosts(
  params: PageParams<CostFilters>,
  signal?: AbortSignal,
): Promise<CursorPage<CostRow>> {
  return requestV2<CursorPage<CostRow>>({ url: '/admin/v2/costs', params, signal })
}
