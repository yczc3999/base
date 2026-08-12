import { toValue } from 'vue'
import { fetchRelease, fetchReleases } from '@/api/v2/releases'
import type { StatusFilters } from '@/api/v2/types'
import {
  useCursorPageQuery,
  useDetailQuery,
  type PageQueryInput,
  type ReactiveValue,
  type V2QueryOptions,
} from './page'

export function useReleasesPage(input: PageQueryInput<StatusFilters>, options?: V2QueryOptions) {
  return useCursorPageQuery('releases', 'list', input, fetchReleases, options)
}

export function useRelease(id: ReactiveValue<string>, options?: V2QueryOptions) {
  return useDetailQuery(
    'releases',
    'detail',
    [id],
    (signal) => fetchRelease(toValue(id), signal),
    options,
  )
}
