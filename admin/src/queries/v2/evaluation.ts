import { fetchLabels, fetchMetrics, fetchPromotions } from '@/api/v2/evaluation'
import type { LabelFilters, StatusFilters } from '@/api/v2/types'
import { useCursorPageQuery, type PageQueryInput, type V2QueryOptions } from './page'

export function useLabelsPage(input: PageQueryInput<LabelFilters>, options?: V2QueryOptions) {
  return useCursorPageQuery('evaluation', 'labels', input, fetchLabels, options)
}

export function useMetricsPage(input: PageQueryInput<StatusFilters>, options?: V2QueryOptions) {
  return useCursorPageQuery('evaluation', 'metrics', input, fetchMetrics, options)
}

export function usePromotionsPage(input: PageQueryInput<StatusFilters>, options?: V2QueryOptions) {
  return useCursorPageQuery('evaluation', 'promotions', input, fetchPromotions, options)
}

/** Compatibility alias now points at the labels endpoint. */
export const useEvaluationPage = useLabelsPage
