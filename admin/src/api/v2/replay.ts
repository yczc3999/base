import { requestV2 } from '../request'
import { pathSegment } from './path'
import type { CursorPage, PageParams, ReplayRow } from './types'

export async function fetchReplay(
  params: PageParams,
  signal?: AbortSignal,
): Promise<CursorPage<ReplayRow>> {
  return requestV2<CursorPage<ReplayRow>>({ url: '/admin/v2/replay', params, signal })
}

export async function fetchReplayDetail(id: string, signal?: AbortSignal): Promise<ReplayRow> {
  return requestV2<ReplayRow>({ url: `/admin/v2/replay/${pathSegment(id)}`, signal })
}
