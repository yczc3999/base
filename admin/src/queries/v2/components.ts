import { toValue } from 'vue'
import { fetchComponent, fetchComponents } from '@/api/v2/components'
import {
  useCursorPageQuery,
  useDetailQuery,
  type PageQueryInput,
  type ReactiveValue,
  type V2QueryOptions,
} from './page'

export function useComponentsPage(input: PageQueryInput, options?: V2QueryOptions) {
  return useCursorPageQuery('components', 'list', input, fetchComponents, options)
}

export function useComponent(id: ReactiveValue<string>, options?: V2QueryOptions) {
  return useDetailQuery(
    'components',
    'detail',
    [id],
    (signal) => fetchComponent(toValue(id), signal),
    options,
  )
}
