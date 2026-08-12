import { requestV2 } from '../request'
import type { CursorPage, PageParams, ComponentRow } from './types'

export async function fetchComponents(
  params: PageParams,
  signal?: AbortSignal,
): Promise<CursorPage<ComponentRow>> {
  return requestV2<CursorPage<ComponentRow>>({ url: '/admin/v2/components', params, signal })
}

export async function fetchComponent(id: string, signal?: AbortSignal): Promise<unknown> {
  return requestV2<unknown>({ url: '/admin/v2/components/{id}'.replace('{id}', id), signal })
}
