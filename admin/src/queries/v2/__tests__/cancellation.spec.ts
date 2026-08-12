import { QueryClient } from '@tanstack/vue-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => {
  const instance = Object.assign(vi.fn(), {
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  })
  return {
    instance,
    refreshPost: vi.fn(),
    messageError: vi.fn(),
    setToken: vi.fn(),
  }
})

vi.mock('axios', () => ({
  default: {
    create: () => mocks.instance,
    post: mocks.refreshPost,
    isCancel: (error: { code?: string }) => error?.code === 'ERR_CANCELED',
  },
}))
vi.mock('@/utils/auth', () => ({
  getToken: () => 'access-token',
  getRefreshToken: () => 'refresh-token',
  setToken: mocks.setToken,
  clearTokens: vi.fn(),
}))
vi.mock('@/utils/nprogress', () => ({ default: { start: vi.fn(), done: vi.fn() } }))
vi.mock('element-plus/es/components/message/index', () => ({
  ElMessage: { error: mocks.messageError },
}))

import { fetchMarkets } from '@/api/v2/markets'
import { requestV2 } from '@/api/request'
import { shouldRetryV2Query } from '../page'

describe('V2 request cancellation and shared auth transport', () => {
  beforeEach(() => {
    mocks.instance.mockReset()
    mocks.refreshPost.mockReset()
    mocks.messageError.mockReset()
    mocks.setToken.mockReset()
  })

  it('passes Query AbortSignal into the shared Axios instance', async () => {
    mocks.instance.mockResolvedValue({
      status: 200,
      data: { code: 0, msg: 'ok', data: { items: [] } },
    })
    const controller = new AbortController()
    await fetchMarkets({ limit: 5 }, controller.signal)
    expect(mocks.instance.mock.calls[0][0].signal).toBe(controller.signal)
  })

  it('uses the existing shared refresh lock and replays an envelope 401 once', async () => {
    mocks.instance
      .mockResolvedValueOnce({ status: 200, data: { code: 401, msg: 'expired', data: null } })
      .mockResolvedValueOnce({ status: 200, data: { code: 0, msg: 'ok', data: { id: '1' } } })
    mocks.refreshPost.mockResolvedValue({
      data: { code: 0, data: { access_token: 'refreshed-token' } },
    })

    await expect(requestV2<{ id: string }>({ url: '/admin/v2/markets/1' })).resolves.toEqual({ id: '1' })
    expect(mocks.refreshPost).toHaveBeenCalledTimes(1)
    expect(mocks.instance).toHaveBeenCalledTimes(2)
    expect(mocks.instance.mock.calls[1][0].headers.Authorization).toBe('Bearer refreshed-token')
    expect(mocks.setToken).toHaveBeenCalledWith('refreshed-token')
  })

  it('does not refresh, retry or toast a canceled Axios request', async () => {
    const canceled = Object.assign(new Error('canceled'), {
      name: 'CanceledError',
      code: 'ERR_CANCELED',
    })
    mocks.instance.mockRejectedValue(canceled)

    await expect(fetchMarkets({ limit: 5 }, new AbortController().signal)).rejects.toBe(canceled)
    expect(mocks.instance).toHaveBeenCalledTimes(1)
    expect(mocks.refreshPost).not.toHaveBeenCalled()
    expect(mocks.messageError).not.toHaveBeenCalled()
    expect(shouldRetryV2Query(0, canceled)).toBe(false)
  })

  it('cancellation leaves no successful cache entry and performs one attempt', async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: shouldRetryV2Query } },
    })
    let attempts = 0
    const pending = client.fetchQuery({
      queryKey: ['cancellation-cache'],
      queryFn: ({ signal }) => new Promise<string>((_resolve, reject) => {
        attempts += 1
        signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
      }),
    })
    await Promise.resolve()
    await client.cancelQueries({ queryKey: ['cancellation-cache'] })
    await pending.catch(() => undefined)

    expect(attempts).toBe(1)
    expect(client.getQueryData(['cancellation-cache'])).toBeUndefined()
    client.clear()
  })
})
