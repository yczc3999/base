import { fetchCosts } from '@/api/v2/costs'
import type { CostFilters } from '@/api/v2/types'
import { useCursorPageQuery, type PageQueryInput, type V2QueryOptions } from './page'

export function useCostsPage(input: PageQueryInput<CostFilters>, options?: V2QueryOptions) {
  return useCursorPageQuery('costs', 'list', input, fetchCosts, options)
}
