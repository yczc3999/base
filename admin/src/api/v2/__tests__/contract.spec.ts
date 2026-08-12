/**
 * WP-07A Checkpoint C —— api module contract（mock axios）。
 *
 * API module 只发请求并解析统一 response；不吞 401/403/contract errors。
 * BIGINT/NUMERIC 保持字符串；CursorPage envelope 严格。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

const mocks = vi.hoisted(() => {
  const fn = Object.assign(vi.fn(), {
      get: vi.fn(),
      post: vi.fn(),
      interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
    })
  return { instance: fn }
})

vi.mock('axios', () => ({
  default: {
    create: () => mocks.instance,
  },
}))

// axios 默认导出被 mock；request.ts 顶层 import 需在模块加载后运行
vi.mock('@/utils/auth', () => ({
  getToken: () => 't',
  getRefreshToken: () => 'r',
  setToken: () => {},
  clearTokens: () => {},
}))

vi.mock('@/utils/nprogress', () => ({
  default: { start: () => {}, done: () => {} },
}))

vi.mock('element-plus/es/components/message/index', () => ({
  ElMessage: { error: vi.fn() },
}))

import { requestV2 } from '../../request'
import { fetchMarkets } from '../markets'

describe('requestV2 contract', () => {
  beforeEach(() => {
    mocks.instance.get.mockReset()
    mocks.instance.post.mockReset()
  })

  it('unwraps ApiResponse envelope', async () => {
    mocks.instance.mockResolvedValue({
      data: { code: 0, msg: 'success', data: { items: [], next_cursor: null, has_more: false } },
    })
    const result = await requestV2<{ items: unknown[] }>({ url: '/admin/v2/markets' })
    expect(result).toEqual({ items: [], next_cursor: null, has_more: false })
  })

  it('throws on non-zero code (does not swallow contract errors)', async () => {
    mocks.instance.mockResolvedValue({ data: { code: 403, msg: '无权限', data: null } })
    await expect(requestV2<unknown>({ url: '/admin/v2/markets' })).rejects.toThrow('无权限')
  })

  it('fetchMarkets sends params and returns typed page', async () => {
    mocks.instance.mockResolvedValue({
      data: {
        code: 0,
        msg: 'success',
        data: {
          items: [{ id: '1', gamma_market_id: '1001', neg_risk: false }],
          next_cursor: 'tok',
          has_more: true,
          as_of: '2026-08-12T00:00:00Z',
          filter_hash: 'a'.repeat(64),
        },
      },
    })
    const page = await fetchMarkets({ limit: 10 })
    expect(page.items[0].id).toBe('1') // BIGINT 字符串
    expect(page.has_more).toBe(true)
    expect(mocks.instance).toHaveBeenCalledWith(
      expect.objectContaining({ url: '/admin/v2/markets', params: expect.objectContaining({ limit: 10 }) }),
    )
  })
})
