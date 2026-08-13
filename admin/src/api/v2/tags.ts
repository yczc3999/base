import { requestV2, requestV2Mutate } from '../request'
import { pathSegment } from './path'
import type {
  CursorPage,
  PageParams,
  TagDisposition,
  TagFilters,
  TagRow,
  TagSyncResult,
} from './types'

export async function fetchTags(
  params: PageParams<TagFilters>,
  signal?: AbortSignal,
): Promise<CursorPage<TagRow>> {
  return requestV2<CursorPage<TagRow>>({ url: '/admin/v2/tags', params, signal })
}

export async function saveTagDisposition(
  id: string,
  disposition: TagDisposition | null,
): Promise<TagRow> {
  return requestV2Mutate<TagRow>({
    method: 'PATCH',
    url: `/admin/v2/tags/${pathSegment(id)}`,
    data: { disposition },
  })
}

export async function syncTagCatalog(): Promise<TagSyncResult> {
  return requestV2Mutate<TagSyncResult>({
    method: 'POST',
    url: '/admin/v2/tags/sync',
    timeout: 120000,
  })
}
