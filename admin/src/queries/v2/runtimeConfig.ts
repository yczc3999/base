import { useMutation, useQueryClient } from '@tanstack/vue-query'
import {
  clearRuntimeCredential,
  fetchRuntimeConfig,
  saveRuntimeCredential,
  setPipelineAiEnabled,
} from '@/api/v2/runtimeConfig'
import type { RuntimeConfigProvider } from '@/api/v2/runtimeConfig'
import { useDetailQuery, type V2QueryOptions } from './page'
import { v2QueryKeys } from './queryKeys'

const DOMAIN = 'runtime-config'

export function useRuntimeConfig(options?: V2QueryOptions) {
  return useDetailQuery(DOMAIN, 'summary', [], fetchRuntimeConfig, options)
}

function useInvalidateRuntimeConfig() {
  const queryClient = useQueryClient()
  return () => queryClient.invalidateQueries({ queryKey: v2QueryKeys.detail(DOMAIN, 'summary') })
}

export function useSaveRuntimeCredential() {
  const invalidate = useInvalidateRuntimeConfig()
  return useMutation({
    mutationFn: (input: { provider: RuntimeConfigProvider; apiKey: string }) => (
      saveRuntimeCredential(input.provider, input.apiKey)
    ),
    onSuccess: invalidate,
  })
}

export function useClearRuntimeCredential() {
  const invalidate = useInvalidateRuntimeConfig()
  return useMutation({
    mutationFn: (provider: RuntimeConfigProvider) => clearRuntimeCredential(provider),
    onSuccess: invalidate,
  })
}

export function useSetPipelineAiEnabled() {
  const invalidate = useInvalidateRuntimeConfig()
  return useMutation({
    mutationFn: (enabled: boolean) => setPipelineAiEnabled(enabled),
    onSuccess: invalidate,
  })
}
