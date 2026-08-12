import { requestV2 } from '../request'
import type { CursorPage, PageParams, EpisodeRow } from './types'

export async function fetchEpisodes(
  params: PageParams,
  signal?: AbortSignal,
): Promise<CursorPage<EpisodeRow>> {
  return requestV2<CursorPage<EpisodeRow>>({ url: '/admin/v2/episodes', params, signal })
}

export async function fetchEpisode(id: string, signal?: AbortSignal): Promise<unknown> {
  return requestV2<unknown>({ url: '/admin/v2/episodes/{id}'.replace('{id}', id), signal })
}
