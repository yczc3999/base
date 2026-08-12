import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => {
  const instance = Object.assign(vi.fn(), {
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  })
  return { instance, refreshPost: vi.fn(), messageError: vi.fn() }
})

vi.mock('axios', () => ({
  default: {
    create: () => mocks.instance,
    post: mocks.refreshPost,
    isCancel: (error: { code?: string }) => error?.code === 'ERR_CANCELED',
  },
}))
vi.mock('@/utils/auth', () => ({
  getToken: () => 't',
  getRefreshToken: () => 'r',
  setToken: vi.fn(),
  clearTokens: vi.fn(),
}))
vi.mock('@/utils/nprogress', () => ({ default: { start: vi.fn(), done: vi.fn() } }))
vi.mock('element-plus/es/components/message/index', () => ({
  ElMessage: { error: mocks.messageError },
}))

import { ApiRequestError, requestV2 } from '../../request'
import { fetchAi, fetchAiDetail } from '../ai'
import { fetchArtifactContent, fetchArtifactMetadata } from '../artifacts'
import { fetchComponent, fetchComponents } from '../components'
import { fetchConfigDetail, fetchConfiguration } from '../configuration'
import { fetchCosts } from '../costs'
import { fetchDashboard } from '../dashboard'
import { fetchDecision, fetchDecisions } from '../decisions'
import { fetchEpisode, fetchEpisodes, fetchEpisodeTimeline } from '../episodes'
import { fetchLabels, fetchMetrics, fetchPromotions } from '../evaluation'
import {
  fetchExecutionTrace,
  fetchIntents,
  fetchLedger,
  fetchOrders,
  fetchPositions,
} from '../execution'
import { fetchAlerts, fetchIntegrityRuntime, fetchIntegrityWorkflow } from '../integrity'
import { fetchMarket, fetchMarkets } from '../markets'
import { fetchModels } from '../models'
import { fetchRelease, fetchReleases } from '../releases'
import { fetchReplay, fetchReplayDetail } from '../replay'
import type { AiInvocationRow, ReplayRow } from '../types'

const OK = { status: 200, data: { code: 0, msg: 'success', data: {} } }

describe('requestV2 contract', () => {
  beforeEach(() => {
    mocks.instance.mockReset()
    mocks.refreshPost.mockReset()
    mocks.messageError.mockReset()
  })

  it('unwraps the Base response and preserves contract error identity without a toast', async () => {
    mocks.instance.mockResolvedValueOnce({
      status: 200,
      data: { code: 0, msg: 'success', data: { id: '1' } },
    })
    await expect(requestV2<{ id: string }>({ url: '/admin/v2/markets/1' })).resolves.toEqual({ id: '1' })

    mocks.instance.mockResolvedValueOnce({
      status: 200,
      data: { code: 403, msg: '无权限', data: null },
    })
    const error = await requestV2({ url: '/admin/v2/markets' }).catch((reason: unknown) => reason)
    expect(error).toBeInstanceOf(ApiRequestError)
    expect((error as ApiRequestError).code).toBe(403)
    expect(mocks.messageError).not.toHaveBeenCalled()
  })

  it('models Dashboard as its real non-paginated response', async () => {
    const dashboard = { blocks: {}, authoritative: {}, as_of: '2026-08-12T00:00:00Z' }
    mocks.instance.mockResolvedValue({
      status: 200,
      data: { code: 0, msg: 'success', data: dashboard },
    })
    await expect(fetchDashboard()).resolves.toEqual(dashboard)
  })

  it('keeps every typed JSON facade in parity with the FastAPI route table', async () => {
    mocks.instance.mockResolvedValue(OK)
    const cases: Array<[string, () => Promise<unknown>]> = [
      ['/admin/v2/dashboard', () => fetchDashboard()],
      ['/admin/v2/markets', () => fetchMarkets({})],
      ['/admin/v2/markets/1', () => fetchMarket('1')],
      ['/admin/v2/components', () => fetchComponents({})],
      ['/admin/v2/components/2', () => fetchComponent('2')],
      ['/admin/v2/episodes', () => fetchEpisodes({})],
      ['/admin/v2/episodes/3', () => fetchEpisode('3')],
      ['/admin/v2/episodes/3/timeline', () => fetchEpisodeTimeline('3', {})],
      ['/admin/v2/decisions', () => fetchDecisions({})],
      ['/admin/v2/decisions/4', () => fetchDecision('4')],
      ['/admin/v2/execution/intents', () => fetchIntents({})],
      ['/admin/v2/execution/orders', () => fetchOrders({})],
      ['/admin/v2/execution/positions', () => fetchPositions({})],
      ['/admin/v2/execution/ledger', () => fetchLedger({})],
      ['/admin/v2/execution/4/trace', () => fetchExecutionTrace('4')],
      ['/admin/v2/model-routes', () => fetchModels({})],
      ['/admin/v2/ai-invocations', () => fetchAi({})],
      ['/admin/v2/ai-invocations/5', () => fetchAiDetail('5', '2026-08-12T00:00:00Z')],
      ['/admin/v2/costs', () => fetchCosts({})],
      ['/admin/v2/strategy-config', () => fetchConfiguration({})],
      ['/admin/v2/strategy-config/6', () => fetchConfigDetail('6')],
      ['/admin/v2/releases', () => fetchReleases({})],
      ['/admin/v2/releases/7', () => fetchRelease('7')],
      ['/admin/v2/evaluation/labels', () => fetchLabels({})],
      ['/admin/v2/evaluation/metrics', () => fetchMetrics({})],
      ['/admin/v2/evaluation/promotions', () => fetchPromotions({})],
      ['/admin/v2/replay', () => fetchReplay({})],
      ['/admin/v2/replay/8', () => fetchReplayDetail('8')],
      ['/admin/v2/integrity/runtime', () => fetchIntegrityRuntime()],
      ['/admin/v2/integrity/alerts', () => fetchAlerts({})],
      ['/admin/v2/integrity/workflows/episode/episode%2F9', () => (
        fetchIntegrityWorkflow('episode', 'episode/9')
      )],
      [`/admin/v2/artifacts/${'a'.repeat(64)}/metadata`, () => (
        fetchArtifactMetadata('a'.repeat(64))
      )],
    ]

    for (const [expectedUrl, invoke] of cases) {
      mocks.instance.mockClear()
      await invoke()
      expect(mocks.instance).toHaveBeenCalledTimes(1)
      expect(mocks.instance.mock.calls[0][0].url).toBe(expectedUrl)
    }
  })

  it('uses the shared transport for a single artifact Range response', async () => {
    const data = new ArrayBuffer(4)
    mocks.instance.mockResolvedValue({
      status: 206,
      data,
      headers: {
        'content-type': 'application/json',
        'content-range': 'bytes 0-3/4',
        'accept-ranges': 'bytes',
        etag: '"hash"',
      },
    })
    const result = await fetchArtifactContent('a'.repeat(64), { start: '0', end: '3' })
    expect(result.data).toBe(data)
    expect(mocks.instance.mock.calls[0][0]).toEqual(expect.objectContaining({
      url: `/admin/v2/artifacts/${'a'.repeat(64)}/content`,
      headers: { Range: 'bytes=0-3' },
      responseType: 'arraybuffer',
    }))
  })

  it('keeps BIGINT values beyond Number.MAX_SAFE_INTEGER as decimal strings', () => {
    const inputTokens: AiInvocationRow['input_tokens'] = '9007199254740993'
    const seed: ReplayRow['seed'] = '9223372036854775807'
    expect(typeof inputTokens).toBe('string')
    expect(typeof seed).toBe('string')
  })
})
