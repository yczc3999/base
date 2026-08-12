import { createSSRApp, h } from 'vue'
import { createPinia } from 'pinia'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { renderToString } from '@vue/server-renderer'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ fetchMarkets: vi.fn() }))

vi.mock('@/api/request', () => ({ isRequestCanceled: () => false }))
vi.mock('@/utils/auth', () => ({
  getToken: () => 'fixture-token',
  setToken: vi.fn(),
  setRefreshToken: vi.fn(),
  clearTokens: vi.fn(),
}))
vi.mock('@/api/v2/markets', () => ({
  fetchMarkets: mocks.fetchMarkets,
  fetchMarket: vi.fn(),
}))

import { useMarketsPage } from '@/queries/v2/markets'
import { useUserStore } from '@/stores/user'

async function renderMarkets(permissions: string[]) {
  let query: ReturnType<typeof useMarketsPage> | undefined
  const app = createSSRApp({
    setup() {
      query = useMarketsPage({ limit: 10 })
      return () => h('div', query?.denied.value ? 'denied' : 'allowed')
    },
  })
  const pinia = createPinia()
  app.use(pinia)
  app.use(VueQueryPlugin, {
    queryClient: new QueryClient({ defaultOptions: { queries: { retry: false } } }),
  })
  const user = useUserStore(pinia)
  user.$patch((state) => {
    state.userInfo = {
      id: 7,
      username: 'operator',
      nickname: 'Operator',
      avatar: '',
      email: '',
      is_super_admin: false,
    }
    state.permissions = permissions
  })
  const html = await renderToString(app)
  return { html, query }
}

describe('V2 permission-gated requests', () => {
  beforeEach(() => mocks.fetchMarkets.mockReset())

  it('renders denied and sends no request when the permission is absent', async () => {
    const { html, query } = await renderMarkets([])
    expect(html).toContain('denied')
    expect(query?.denied.value).toBe(true)
    expect(mocks.fetchMarkets).not.toHaveBeenCalled()
  })

  it('allows the query when the exact view permission is present', async () => {
    mocks.fetchMarkets.mockResolvedValue({
      items: [],
      next_cursor: null,
      has_more: false,
      as_of: '2026-08-12T00:00:00Z',
      filter_hash: 'a'.repeat(64),
    })
    const { query } = await renderMarkets(['v2:markets:view'])
    expect(query?.denied.value).toBe(false)
    expect(mocks.fetchMarkets).toHaveBeenCalledTimes(1)
  })
})
