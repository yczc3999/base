import { requestV2 } from '../request'
import type { CursorPage, PageParams, EvaluationRow } from './types'

export async function fetchEvaluation(
  params: PageParams,
  signal?: AbortSignal,
): Promise<CursorPage<EvaluationRow>> {
  return requestV2<CursorPage<EvaluationRow>>({ url: '/admin/v2/evaluation', params, signal })
}
