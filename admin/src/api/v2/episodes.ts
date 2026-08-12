import { requestV2 } from '../request'
import { pathSegment } from './path'
import type {
  CursorPage,
  EpisodeDetail,
  EpisodeFilters,
  EpisodeRow,
  PageParams,
  TimelineRow,
} from './types'

export async function fetchEpisodes(
  params: PageParams<EpisodeFilters>,
  signal?: AbortSignal,
): Promise<CursorPage<EpisodeRow>> {
  return requestV2<CursorPage<EpisodeRow>>({ url: '/admin/v2/episodes', params, signal })
}

export async function fetchEpisode(id: string, signal?: AbortSignal): Promise<EpisodeDetail> {
  return requestV2<EpisodeDetail>({ url: `/admin/v2/episodes/${pathSegment(id)}`, signal })
}

export async function fetchEpisodeTimeline(
  id: string,
  params: PageParams,
  signal?: AbortSignal,
): Promise<CursorPage<TimelineRow>> {
  return requestV2<CursorPage<TimelineRow>>({
    url: `/admin/v2/episodes/${pathSegment(id)}/timeline`,
    params,
    signal,
  })
}
