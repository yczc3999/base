<script setup lang="ts">
/** 运行时配置：4 个模型 API Key（写只进、读只回掩码）+ pipeline AI 开关。 */
import { computed, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index'
import PageShell from '@/components/PageShell/index.vue'
import { PageState, StatusBadge } from '../_shared'
import {
  useClearRuntimeCredential,
  useRuntimeConfig,
  useSaveRuntimeCredential,
  useSetPipelineAiEnabled,
} from '@/queries/v2/runtimeConfig'
import type {
  CredentialSource,
  RuntimeConfigProvider,
  RuntimeCredentialStatus,
} from '@/api/v2/runtimeConfig'
import { ApiRequestError } from '@/api/request'

const PROVIDERS: ReadonlyArray<{ id: RuntimeConfigProvider; label: string }> = [
  { id: 'deepseek', label: 'DeepSeek' },
  { id: 'xai', label: 'xAI (Grok)' },
  { id: 'kimi', label: 'Kimi' },
  { id: 'packy', label: 'Packy' },
]

const SOURCE_LABEL: Record<CredentialSource, string> = {
  db: '数据库',
  env: '环境变量',
  unset: '未配置',
}

const { data, isLoading, displayError, denied, refetch } = useRuntimeConfig()
const credentials = computed(() => data.value?.credentials ?? [])
const aiFlag = computed(() => data.value?.flags['pipeline.ai_enabled'])

const drafts = reactive<Record<RuntimeConfigProvider, string>>({
  deepseek: '', xai: '', kimi: '', packy: '',
})
/** 正在写库的 provider，逐行禁用避免并发写。 */
const pendingProvider = ref<RuntimeConfigProvider | null>(null)
const flagPending = ref(false)

const saveMutation = useSaveRuntimeCredential()
const clearMutation = useClearRuntimeCredential()
const flagMutation = useSetPipelineAiEnabled()

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiRequestError ? error.message : fallback
}

function statusOf(provider: RuntimeConfigProvider): RuntimeCredentialStatus | undefined {
  return credentials.value.find((item) => item.provider === provider)
}

function badgeTone(status: RuntimeCredentialStatus | undefined): 'success' | 'info' | 'neutral' {
  if (!status?.configured) return 'neutral'
  return status.source === 'db' ? 'success' : 'info'
}

async function onSave(provider: RuntimeConfigProvider) {
  const apiKey = drafts[provider].trim()
  if (!apiKey || pendingProvider.value) return
  pendingProvider.value = provider
  try {
    await saveMutation.mutateAsync({ provider, apiKey })
    drafts[provider] = ''
    ElMessage.success(`${provider} 密钥已保存，运行时即时生效`)
  } catch (error) {
    ElMessage.error(errorMessage(error, '保存失败'))
  } finally {
    pendingProvider.value = null
  }
}

async function onClear(provider: RuntimeConfigProvider) {
  if (pendingProvider.value) return
  pendingProvider.value = provider
  try {
    await clearMutation.mutateAsync(provider)
    ElMessage.success(`${provider} 已清除数据库密钥，回退环境变量`)
  } catch (error) {
    ElMessage.error(errorMessage(error, '清除失败'))
  } finally {
    pendingProvider.value = null
  }
}

async function onToggleAi(value: string | number | boolean) {
  if (flagPending.value) return
  const enabled = Boolean(value)
  flagPending.value = true
  try {
    await flagMutation.mutateAsync(enabled)
    ElMessage.success(enabled ? 'AI 决策链已开启' : 'AI 决策链已关闭')
  } catch (error) {
    ElMessage.error(errorMessage(error, '开关更新失败'))
  } finally {
    flagPending.value = false
  }
}
</script>

<template>
  <PageShell class="v2-page" title="运行时配置" sub-title="模型 API Key · 写只进 · pipeline AI 开关">
    <PageState
      :loading="isLoading"
      :error="displayError"
      :denied="denied"
      :empty="false"
      @retry="refetch"
    >
      <section class="rc-section">
        <h3 class="rc-title">模型 API Key</h3>
        <p class="rc-hint">密钥只写入不回显；已配置项仅显示尾号。数据库配置优先于环境变量，保存后无需重启。</p>
        <div v-for="provider in PROVIDERS" :key="provider.id" class="rc-row">
          <div class="rc-row-head">
            <span class="rc-name">{{ provider.label }}</span>
            <StatusBadge :tone="badgeTone(statusOf(provider.id))">
              {{ SOURCE_LABEL[statusOf(provider.id)?.source ?? 'unset'] }}
            </StatusBadge>
          </div>
          <div class="rc-meta mono">
            <template v-if="statusOf(provider.id)?.configured">
              <span>尾号 ····{{ statusOf(provider.id)?.last4 ?? '????' }}</span>
              <span v-if="statusOf(provider.id)?.version_no != null">v{{ statusOf(provider.id)?.version_no }}</span>
              <span v-if="statusOf(provider.id)?.updated_at">更新于 {{ statusOf(provider.id)?.updated_at }}</span>
            </template>
            <template v-else>未配置 — 该 provider 的 AI 调用会以 model_credential_missing 失败</template>
          </div>
          <div class="rc-actions">
            <el-input
              v-model="drafts[provider.id]"
              class="rc-input"
              type="password"
              show-password
              clearable
              placeholder="粘贴新密钥（保存后不回显）"
              :disabled="pendingProvider !== null"
              @keyup.enter="onSave(provider.id)"
            />
            <el-button
              type="primary"
              :disabled="!drafts[provider.id].trim() || pendingProvider !== null"
              :loading="pendingProvider === provider.id"
              @click="onSave(provider.id)"
            >保存</el-button>
            <el-button
              :disabled="statusOf(provider.id)?.source !== 'db' || pendingProvider !== null"
              :loading="pendingProvider === provider.id"
              @click="onClear(provider.id)"
            >清除</el-button>
          </div>
        </div>
      </section>

      <section class="rc-section">
        <h3 class="rc-title">AI 决策链开关</h3>
        <div class="rc-flag">
          <el-switch
            :model-value="aiFlag?.value ?? false"
            :loading="flagPending"
            :disabled="flagPending"
            active-text="开启"
            inactive-text="关闭"
            @change="onToggleAi"
          />
          <span class="rc-flag-src">
            当前{{ aiFlag?.value ? '开启' : '关闭' }} · 生效来源：{{
              aiFlag?.source === 'db' ? '数据库覆盖' : '环境变量默认（PM_V2_PIPELINE_AI_ENABLED）'
            }}
          </span>
        </div>
        <p class="rc-hint">开关写入数据库后 pipeline 下一轮即按新值执行；「清除」语义不存在于此开关，删除覆盖需由后端操作。</p>
      </section>
    </PageState>
  </PageShell>
</template>

<style scoped>
.rc-section{border:var(--v2-border-w) solid var(--v2-line);background:var(--v2-surface);border-radius:var(--v2-radius-md);padding:var(--v2-space-4);margin-bottom:var(--v2-space-4)}
.rc-title{margin:0 0 var(--v2-space-2);font-size:15px;font-weight:700}
.rc-hint{margin:0 0 var(--v2-space-3);font-size:12.5px;color:var(--v2-ink-muted)}
.rc-row{border-top:var(--v2-border-w) solid var(--v2-line);padding:var(--v2-space-3) 0}
.rc-row:first-of-type{border-top:none}
.rc-row-head{display:flex;align-items:center;gap:var(--v2-space-3);margin-bottom:var(--v2-space-1)}
.rc-name{font-weight:600}
.rc-meta{font-size:12px;color:var(--v2-ink-muted);display:flex;gap:var(--v2-space-3);flex-wrap:wrap;margin-bottom:var(--v2-space-2)}
.mono{font-family:var(--v2-font-mono)}
.rc-actions{display:flex;gap:var(--v2-space-2);align-items:center}
.rc-input{max-width:420px}
.rc-flag{display:flex;align-items:center;gap:var(--v2-space-3)}
.rc-flag-src{font-size:12.5px;color:var(--v2-ink-muted)}
</style>
