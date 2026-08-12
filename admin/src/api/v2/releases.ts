import { requestV2 } from '../request'
import { pathSegment } from './path'
import type { CursorPage, PageParams, ReleaseDetail, ReleaseRow, StatusFilters } from './types'

export function fetchReleases(
  params: PageParams<StatusFilters>,
  signal?: AbortSignal,
): Promise<CursorPage<ReleaseRow>> {
  return requestV2({ url: '/admin/v2/releases', params, signal })
}

export function fetchRelease(id: string, signal?: AbortSignal): Promise<ReleaseDetail> {
  return requestV2({ url: `/admin/v2/releases/${pathSegment(id)}`, signal })
}
