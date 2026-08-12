import { requestV2 } from '../request'
import { pathSegment } from './path'
import type { ConfigDetail, ConfigRow, CursorPage, PageParams, StatusFilters } from './types'

export async function fetchConfiguration(
  params: PageParams<StatusFilters>,
  signal?: AbortSignal,
): Promise<CursorPage<ConfigRow>> {
  return requestV2<CursorPage<ConfigRow>>({ url: '/admin/v2/strategy-config', params, signal })
}

export async function fetchConfigDetail(id: string, signal?: AbortSignal): Promise<ConfigDetail> {
  return requestV2<ConfigDetail>({ url: `/admin/v2/strategy-config/${pathSegment(id)}`, signal })
}
