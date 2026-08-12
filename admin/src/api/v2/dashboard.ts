import { requestV2 } from '../request'
import type { DashboardResponse } from './types'

export async function fetchDashboard(signal?: AbortSignal): Promise<DashboardResponse> {
  return requestV2<DashboardResponse>({ url: '/admin/v2/dashboard', signal })
}
