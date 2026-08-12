import { fetchDashboard } from '@/api/v2/dashboard'
import { useDetailQuery, type V2QueryOptions } from './page'

export function useDashboard(options?: V2QueryOptions) {
  return useDetailQuery('dashboard', 'summary', [], fetchDashboard, options)
}

/** Compatibility name retained while Dashboard is correctly modeled as a non-page response. */
export const useDashboardPage = useDashboard
