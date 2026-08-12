import { requestV2 } from '../request'
import type { CursorPage, PageParams, DashboardResponse } from './types'

export async function fetchDashboard(
  params: PageParams,
  signal?: AbortSignal,
): Promise<CursorPage<DashboardResponse>> {
  return requestV2<CursorPage<DashboardResponse>>({ url: '/admin/v2/dashboard', params, signal })
}
