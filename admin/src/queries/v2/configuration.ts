import { toValue } from 'vue'
import { fetchConfigDetail, fetchConfiguration } from '@/api/v2/configuration'
import type { StatusFilters } from '@/api/v2/types'
import {
  useCursorPageQuery,
  useDetailQuery,
  type PageQueryInput,
  type ReactiveValue,
  type V2QueryOptions,
} from './page'

export function useConfigurationPage(
  input: PageQueryInput<StatusFilters>,
  options?: V2QueryOptions,
) {
  return useCursorPageQuery('configuration', 'list', input, fetchConfiguration, options)
}

export function useConfigDetail(id: ReactiveValue<string>, options?: V2QueryOptions) {
  return useDetailQuery(
    'configuration',
    'detail',
    [id],
    (signal) => fetchConfigDetail(toValue(id), signal),
    options,
  )
}
