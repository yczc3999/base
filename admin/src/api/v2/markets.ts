import { requestV2 } from '../request'
import { pathSegment } from './path'
import type { CursorPage, MarketDetail, MarketFilters, MarketRow, PageParams } from './types'

export async function fetchMarkets(
  params: PageParams<MarketFilters>,
  signal?: AbortSignal,
): Promise<CursorPage<MarketRow>> {
  return requestV2<CursorPage<MarketRow>>({ url: '/admin/v2/markets', params, signal })
}

export async function fetchMarket(id: string, signal?: AbortSignal): Promise<MarketDetail> {
  return requestV2<MarketDetail>({ url: `/admin/v2/markets/${pathSegment(id)}`, signal })
}
