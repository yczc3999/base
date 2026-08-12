import { requestV2 } from '../request'
import type { CursorPage, PageParams, ConfigRow } from './types'

export async function fetchConfiguration(
  params: PageParams,
  signal?: AbortSignal,
): Promise<CursorPage<ConfigRow>> {
  return requestV2<CursorPage<ConfigRow>>({ url: '/admin/v2/strategy-config', params, signal })
}

export async function fetchConfigDetail(id: string, signal?: AbortSignal): Promise<unknown> {
  return requestV2<unknown>({ url: '/admin/v2/strategy-config/{id}'.replace('{id}', id), signal })
}
