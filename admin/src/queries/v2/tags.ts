import { fetchTags } from '@/api/v2/tags'
import type { TagFilters } from '@/api/v2/types'
import { useCursorPageQuery, type PageQueryInput, type V2QueryOptions } from './page'

export function useTagsPage(
  input: PageQueryInput<TagFilters>,
  options?: V2QueryOptions,
) {
  return useCursorPageQuery('tags', 'list', input, fetchTags, options)
}
