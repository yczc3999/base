import { requestV2 } from '../request'
import { pathSegment } from './path'
import type { ComponentDetail, ComponentRow, CursorPage, PageParams } from './types'

export async function fetchComponents(
  params: PageParams,
  signal?: AbortSignal,
): Promise<CursorPage<ComponentRow>> {
  return requestV2<CursorPage<ComponentRow>>({ url: '/admin/v2/components', params, signal })
}

export async function fetchComponent(id: string, signal?: AbortSignal): Promise<ComponentDetail> {
  return requestV2<ComponentDetail>({ url: `/admin/v2/components/${pathSegment(id)}`, signal })
}
