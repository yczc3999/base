import { toValue } from 'vue'
import { fetchReplay, fetchReplayDetail } from '@/api/v2/replay'
import {
  useCursorPageQuery,
  useDetailQuery,
  type PageQueryInput,
  type ReactiveValue,
  type V2QueryOptions,
} from './page'

export function useReplayPage(input: PageQueryInput, options?: V2QueryOptions) {
  return useCursorPageQuery('replay', 'list', input, fetchReplay, options)
}

export function useReplayDetail(id: ReactiveValue<string>, options?: V2QueryOptions) {
  return useDetailQuery(
    'replay',
    'detail',
    [id],
    (signal) => fetchReplayDetail(toValue(id), signal),
    options,
  )
}
