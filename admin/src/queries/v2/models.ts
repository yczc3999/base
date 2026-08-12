import { fetchModels } from '@/api/v2/models'
import { useCursorPageQuery, type PageQueryInput, type V2QueryOptions } from './page'

export function useModelsPage(input: PageQueryInput, options?: V2QueryOptions) {
  return useCursorPageQuery('models', 'model-routes', input, fetchModels, options)
}
