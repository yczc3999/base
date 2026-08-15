import { createSSRApp, h } from 'vue'
import { createPinia } from 'pinia'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { renderToString } from '@vue/server-renderer'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  fetchRuntimeConfig: vi.fn(),
  saveRuntimeCredential: vi.fn(),
  clearRuntimeCredential: vi.fn(),
  setPipelineAiEnabled: vi.fn(),
}))

vi.mock('@/api/request', () => ({ isRequestCanceled: () => false }))
vi.mock('@/utils/auth', () => ({
  getToken: () => 'fixture-token',
  setToken: vi.fn(),
  setRefreshToken: vi.fn(),
  clearTokens: vi.fn(),
}))
vi.mock('@/api/v2/runtimeConfig', () => mocks)

import {
  useClearRuntimeCredential,
  useRuntimeConfig,
  useSaveRuntimeCredential,
  useSetPipelineAiEnabled,
} from '@/queries/v2/runtimeConfig'
import { useUserStore } from '@/stores/user'

const FIXTURE = {
  credentials: [
    {
      provider: 'kimi',
      configured: true,
      source: 'db',
      last4: 'wxyz',
      version_no: 2,
      updated_at: '2026-08-15T00:00:00Z',
    },
  ],
  flags: { 'pipeline.ai_enabled': { value: false, source: 'default' } },
}

function renderRuntimeConfig(permissions: string[]) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')
  const exposed: {
    query?: ReturnType<typeof useRuntimeConfig>
    save?: ReturnType<typeof useSaveRuntimeCredential>
    clear?: ReturnType<typeof useClearRuntimeCredential>
    flag?: ReturnType<typeof useSetPipelineAiEnabled>
  } = {}
  const app = createSSRApp({
    setup() {
      exposed.query = useRuntimeConfig()
      exposed.save = useSaveRuntimeCredential()
      exposed.clear = useClearRuntimeCredential()
      exposed.flag = useSetPipelineAiEnabled()
      return () => h('div', exposed.query?.denied.value ? 'denied' : 'allowed')
    },
  })
  const pinia = createPinia()
  app.use(pinia)
  app.use(VueQueryPlugin, { queryClient })
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
  return { app, exposed, invalidateSpy }
}

describe('useRuntimeConfig', () => {
  beforeEach(() => {
    mocks.fetchRuntimeConfig.mockReset()
    mocks.saveRuntimeCredential.mockReset()
    mocks.clearRuntimeCredential.mockReset()
    mocks.setPipelineAiEnabled.mockReset()
  })

  it('renders denied and sends no request when the permission is absent', async () => {
    const { app, exposed } = renderRuntimeConfig([])
    const html = await renderToString(app)
    expect(html).toContain('denied')
    expect(exposed.query?.denied.value).toBe(true)
    expect(mocks.fetchRuntimeConfig).not.toHaveBeenCalled()
  })

  it('allows the query when v2:runtime-config:view is present', async () => {
    mocks.fetchRuntimeConfig.mockResolvedValue(FIXTURE)
    const { app, exposed } = renderRuntimeConfig(['v2:runtime-config:view'])
    await renderToString(app)
    expect(exposed.query?.denied.value).toBe(false)
    expect(mocks.fetchRuntimeConfig).toHaveBeenCalledTimes(1)
  })

  it('mutations hit the API facade and invalidate the runtime-config query', async () => {
    mocks.fetchRuntimeConfig.mockResolvedValue(FIXTURE)
    mocks.saveRuntimeCredential.mockResolvedValue(FIXTURE.credentials[0])
    mocks.clearRuntimeCredential.mockResolvedValue(null)
    mocks.setPipelineAiEnabled.mockResolvedValue(FIXTURE.flags['pipeline.ai_enabled'])
    const { app, exposed, invalidateSpy } = renderRuntimeConfig(['v2:runtime-config:view'])
    await renderToString(app)

    await exposed.save?.mutateAsync({ provider: 'kimi', apiKey: 'sk-fixture' })
    expect(mocks.saveRuntimeCredential).toHaveBeenCalledWith('kimi', 'sk-fixture')
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ['v2-admin-read', 'runtime-config', 'summary'],
    })

    await exposed.clear?.mutateAsync('deepseek')
    expect(mocks.clearRuntimeCredential).toHaveBeenCalledWith('deepseek')

    await exposed.flag?.mutateAsync(true)
    expect(mocks.setPipelineAiEnabled).toHaveBeenCalledWith(true)
    expect(invalidateSpy).toHaveBeenCalledTimes(3)
  })
})
