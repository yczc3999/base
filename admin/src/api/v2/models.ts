import { requestV2 } from '../request'
import type { CursorPage, PageParams, ModelRouteRow } from './types'

export async function fetchModels(
  params: PageParams,
  signal?: AbortSignal,
): Promise<CursorPage<ModelRouteRow>> {
  return requestV2<CursorPage<ModelRouteRow>>({ url: '/admin/v2/model-routes', params, signal })
}
