import { requestV2 } from '../request'
import type { CursorPage, PageParams, MarketRow } from './types'

export async function fetchMarkets(
  params: PageParams,
  signal?: AbortSignal,
): Promise<CursorPage<MarketRow>> {
  return requestV2<CursorPage<MarketRow>>({ url: '/admin/v2/markets', params, signal })
}

export async function fetchMarket(id: string, signal?: AbortSignal): Promise<unknown> {
  return requestV2<unknown>({ url: '/admin/v2/markets/{id}'.replace('{id}', id), signal })
}
