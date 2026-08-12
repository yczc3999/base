import { toValue } from 'vue'
import { fetchMarket, fetchMarkets } from '@/api/v2/markets'
import type { MarketFilters } from '@/api/v2/types'
import {
  useCursorPageQuery,
  useDetailQuery,
  type PageQueryInput,
  type ReactiveValue,
  type V2QueryOptions,
} from './page'

export function useMarketsPage(
  input: PageQueryInput<MarketFilters>,
  options?: V2QueryOptions,
) {
  return useCursorPageQuery('markets', 'list', input, fetchMarkets, options)
}

export function useMarket(id: ReactiveValue<string>, options?: V2QueryOptions) {
  return useDetailQuery(
    'markets',
    'detail',
    [id],
    (signal) => fetchMarket(toValue(id), signal),
    options,
  )
}
