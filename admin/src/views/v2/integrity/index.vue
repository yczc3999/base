<script setup lang="ts">
/** WP-07B Integrity：runtime + Alerts keyset + workflow/external-call aggregate lookup。 */
import { ref, watch, computed } from 'vue'
import PageShell from '@/components/PageShell/index.vue'
import { DetailSection, PageState, StatusBadge } from '../_shared'
import { useAlertsPage, useIntegrityRuntime, useIntegrityWorkflow } from '@/queries/v2/integrity'
import type { WorkflowAggregateType } from '@/api/v2/types'

const active = ref('alerts')
const rt = useIntegrityRuntime()

const alertFilters = ref<Record<string, string>>({})
const alertCursor = ref<string | null>(null)
const alertAsOf = ref<string | null>(null)
const alerts = useAlertsPage({
  filters: alertFilters,
  cursor: alertCursor,
  asOf: alertAsOf,
  limit: 50,
})
watch(alertFilters, () => { alertCursor.value = null; alertAsOf.value = null }, { deep: true })
const alertRows = computed(() => alerts.data.value?.items ?? [])
const alertHasMore = computed(() => alerts.data.value?.has_more ?? false)
function nextAlertPage() {
  alertCursor.value = alerts.data.value?.next_cursor ?? null
  alertAsOf.value = alerts.data.value?.as_of ?? null
}

const lookupType = ref<WorkflowAggregateType>('episode')
const lookupId = ref('')
const submittedType = ref<WorkflowAggregateType>('episode')
const submittedId = ref('')
const lookupEnabled = computed(() => submittedId.value.length > 0)
const workflow = useIntegrityWorkflow(submittedType, submittedId, { enabled: lookupEnabled })
function lookup() {
  const id = lookupId.value.trim()
  if (!id) return
  submittedType.value = lookupType.value
  submittedId.value = id
}
const chainRows = computed(() => active.value === 'workflows'
  ? workflow.data.value?.workflows ?? []
  : workflow.data.value?.external_calls ?? [])
</script>

<template>
  <PageShell class="v2-page" title="Integrity" :loading="alerts.isLoading.value" sub-title="runtime · alerts · workflows · external calls">
    <DetailSection title="Runtime">
      <p class="mono">{{ JSON.stringify(rt.data.value ?? null) }}</p>
    </DetailSection>

    <el-tabs v-model="active" class="v2-tabs">
      <el-tab-pane label="Alerts" name="alerts" />
      <el-tab-pane label="Workflows" name="workflows" />
      <el-tab-pane label="External Calls" name="external-calls" />
    </el-tabs>

    <template v-if="active === 'alerts'">
      <div class="filterbar">
        <el-select v-model="alertFilters.severity" clearable placeholder="severity">
          <el-option v-for="value in ['INFO', 'WARNING', 'CRITICAL']" :key="value" :label="value" :value="value" />
        </el-select>
      </div>
      <PageState
        :loading="alerts.isLoading.value"
        :error="alerts.displayError.value"
        :denied="alerts.denied.value"
        :empty="!alerts.isLoading.value && !alerts.isError.value && !alertRows.length"
        @retry="() => alerts.refetch()"
      >
        <el-table v-loading="alerts.isLoading.value" :data="alertRows" stripe>
          <el-table-column label="alert_key" prop="alert_key" min-width="180" />
          <el-table-column label="severity" min-width="110"><template #default="{ row }">
            <StatusBadge :tone="row.severity === 'CRITICAL' ? 'danger' : row.severity === 'WARNING' ? 'warning' : 'info'">{{ row.severity }}</StatusBadge>
          </template></el-table-column>
          <el-table-column label="code" prop="code" min-width="140" />
          <el-table-column label="message" prop="message_redacted" min-width="220" />
          <el-table-column label="id" prop="id" min-width="90"><template #default="{ row }"><span class="mono">{{ row.id }}</span></template></el-table-column>
        </el-table>
        <div class="pager">
          <span class="muted">{{ alertRows.length }} 条 · as_of {{ alerts.data.value?.as_of }}</span>
          <button class="link-btn" :disabled="!alertHasMore || alerts.isLoading.value" @click="nextAlertPage">下一页 ›</button>
        </div>
      </PageState>
    </template>

    <template v-else>
      <div class="lookupbar">
        <el-select v-model="lookupType" aria-label="aggregate type">
          <el-option
            v-for="value in ['episode','decision','intent','chain_operation','forecast_submission','evidence_bundle']"
            :key="value" :label="value" :value="value"
          />
        </el-select>
        <el-input v-model="lookupId" clearable placeholder="aggregate id" @keyup.enter="lookup" />
        <el-button type="primary" :disabled="!lookupId.trim()" @click="lookup">查询链</el-button>
      </div>
      <p v-if="!lookupEnabled" class="lookup-hint">输入 aggregate type 与 ID，读取同一条 workflow / external-call 审计链。</p>
      <PageState
        v-else
        :loading="workflow.isLoading.value"
        :error="workflow.displayError.value"
        :denied="workflow.denied.value"
        :empty="!workflow.isLoading.value && !workflow.isError.value && !chainRows.length"
        @retry="() => workflow.refetch()"
      >
        <DetailSection :title="active === 'workflows' ? 'Workflow Events' : 'External Calls'">
          <table class="mini">
            <tbody>
              <tr v-for="(row, index) in chainRows" :key="index">
                <td class="mono">{{ JSON.stringify(row) }}</td>
              </tr>
            </tbody>
          </table>
        </DetailSection>
      </PageState>
    </template>
  </PageShell>
</template>

<style scoped>
.v2-tabs :deep(.el-tabs__item){color:var(--v2-ink-muted)}
.v2-tabs :deep(.el-tabs__item.is-active){color:var(--v2-primary)}
.filterbar,.lookupbar{display:flex;gap:var(--v2-space-2);margin-bottom:var(--v2-space-4);flex-wrap:wrap}
.filterbar .el-select,.lookupbar .el-select,.lookupbar .el-input{width:190px}
.lookup-hint,.muted{color:var(--v2-ink-muted);font-size:12.5px}
.mono{font-family:var(--v2-font-mono);font-size:12px;word-break:break-all}
.mini{border-collapse:collapse;width:100%}.mini td{border-bottom:1px solid var(--v2-line);padding:6px 8px}
.pager{display:flex;justify-content:space-between;align-items:center;margin-top:var(--v2-space-3)}
.link-btn{background:none;border:none;color:var(--v2-primary);text-decoration:underline;cursor:pointer;height:var(--v2-control-h)}
.link-btn:disabled{color:var(--v2-ink-muted);cursor:not-allowed;text-decoration:none}
</style>
