import { requestV2 } from '../request'
import type { CursorPage, PageParams, ReplayRow } from './types'

export async function fetchReplay(
  params: PageParams,
  signal?: AbortSignal,
): Promise<CursorPage<ReplayRow>> {
  return requestV2<CursorPage<ReplayRow>>({ url: '/admin/v2/replay', params, signal })
}

export async function fetchReplayDetail(id: string, signal?: AbortSignal): Promise<unknown> {
  return requestV2<unknown>({ url: '/admin/v2/replay/{id}'.replace('{id}', id), signal })
}
