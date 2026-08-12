import { toValue } from 'vue'
import { fetchEpisode, fetchEpisodes, fetchEpisodeTimeline } from '@/api/v2/episodes'
import type { EpisodeFilters } from '@/api/v2/types'
import {
  useCursorPageQuery,
  useDetailQuery,
  type PageQueryInput,
  type ReactiveValue,
  type V2QueryOptions,
} from './page'

export function useEpisodesPage(
  input: PageQueryInput<EpisodeFilters>,
  options?: V2QueryOptions,
) {
  return useCursorPageQuery('episodes', 'list', input, fetchEpisodes, options)
}

export function useEpisode(id: ReactiveValue<string>, options?: V2QueryOptions) {
  return useDetailQuery(
    'episodes',
    'detail',
    [id],
    (signal) => fetchEpisode(toValue(id), signal),
    options,
  )
}

export function useEpisodeTimeline(
  id: ReactiveValue<string>,
  input: PageQueryInput,
  options?: V2QueryOptions,
) {
  return useCursorPageQuery(
    'episodes',
    () => `timeline:${toValue(id)}`,
    input,
    (params, signal) => fetchEpisodeTimeline(toValue(id), params, signal),
    options,
  )
}
