import { requestV2 } from '../request'
import type {
  CursorPage,
  LabelFilters,
  MetricRunRow,
  PageParams,
  PromotionRow,
  ResolutionLabelRow,
  StatusFilters,
} from './types'

export function fetchLabels(
  params: PageParams<LabelFilters>,
  signal?: AbortSignal,
): Promise<CursorPage<ResolutionLabelRow>> {
  return requestV2({ url: '/admin/v2/evaluation/labels', params, signal })
}

export function fetchMetrics(
  params: PageParams<StatusFilters>,
  signal?: AbortSignal,
): Promise<CursorPage<MetricRunRow>> {
  return requestV2({ url: '/admin/v2/evaluation/metrics', params, signal })
}

export function fetchPromotions(
  params: PageParams<StatusFilters>,
  signal?: AbortSignal,
): Promise<CursorPage<PromotionRow>> {
  return requestV2({ url: '/admin/v2/evaluation/promotions', params, signal })
}
