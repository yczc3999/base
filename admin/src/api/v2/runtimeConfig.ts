import { requestV2, requestV2Mutate } from '../request'
import { pathSegment } from './path'
import type { UtcIsoString } from './types'

/** 运行时配置（模型 API Key + AI 开关）：key 写只进，读只回掩码尾号。 */
export type RuntimeConfigProvider = 'deepseek' | 'xai' | 'kimi' | 'packy'

export type CredentialSource = 'db' | 'env' | 'unset'

export interface RuntimeCredentialStatus {
  provider: RuntimeConfigProvider
  configured: boolean
  source: CredentialSource
  last4: string | null
  version_no: number | null
  updated_at: UtcIsoString | null
}

/** 写接口返回的子集（不回 last4/updated_at，页面靠 invalidate 重取）。 */
export interface RuntimeCredentialMutationResult {
  provider: RuntimeConfigProvider
  configured: boolean
  source: CredentialSource
  version_no?: number | null
}

export interface PipelineAiFlag {
  value: boolean
  source: 'db' | 'default'
}

export interface PipelineAiFlagMutationResult extends PipelineAiFlag {
  flag_key: string
  updated_at: UtcIsoString
  updated_by: string
}

export interface RuntimeConfig {
  credentials: RuntimeCredentialStatus[]
  flags: { 'pipeline.ai_enabled': PipelineAiFlag }
}

export async function fetchRuntimeConfig(signal?: AbortSignal): Promise<RuntimeConfig> {
  return requestV2<RuntimeConfig>({ url: '/admin/v2/runtime-config', signal })
}

export async function saveRuntimeCredential(
  provider: RuntimeConfigProvider,
  apiKey: string,
): Promise<RuntimeCredentialMutationResult> {
  return requestV2Mutate<RuntimeCredentialMutationResult>({
    method: 'PUT',
    url: `/admin/v2/runtime-config/credentials/${pathSegment(provider)}`,
    data: { api_key: apiKey },
  })
}

export async function clearRuntimeCredential(
  provider: RuntimeConfigProvider,
): Promise<RuntimeCredentialMutationResult> {
  return requestV2Mutate<RuntimeCredentialMutationResult>({
    method: 'DELETE',
    url: `/admin/v2/runtime-config/credentials/${pathSegment(provider)}`,
  })
}

export async function setPipelineAiEnabled(
  enabled: boolean,
): Promise<PipelineAiFlagMutationResult> {
  return requestV2Mutate<PipelineAiFlagMutationResult>({
    method: 'PUT',
    url: '/admin/v2/runtime-config/flags/pipeline-ai-enabled',
    data: { enabled },
  })
}
