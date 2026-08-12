import { requestV2 } from '../request'
import type { CursorPage, PageParams, IntegrityRow } from './types'

export async function fetchIntegrity(
  params: PageParams,
  signal?: AbortSignal,
): Promise<CursorPage<IntegrityRow>> {
  return requestV2<CursorPage<IntegrityRow>>({ url: '/admin/v2/integrity', params, signal })
}
