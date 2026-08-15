<script setup lang="ts">
/** WP-07B Integrity：runtime + Alerts keyset + workflow/external-call aggregate lookup。 */
import { ref, computed } from 'vue'
import PageShell from '@/components/PageShell/index.vue'
import { DetailSection, KeysetTable, PageState, useKeysetList } from '../_shared'
import { useAlertsPage, useIntegrityRuntime, useIntegrityWorkflow } from '@/queries/v2/integrity'
import type { WorkflowAggregateType } from '@/api/v2/types'
import type { SearchField } from '@/components/CrudTable/types'

const active = ref('alerts')
const rt = useIntegrityRuntime()

const {
  applied: alertApplied, cursor: alertCursor, asOf: alertAsOf, limit: alertLimit,
  page: alertPage, canPrev: alertCanPrev, applyFilters: applyAlertFilters,
  resetFilters: resetAlertFilters, next: nextAlert, prev: prevAlert, setLimit: setAlertLimit,
} = useKeysetList()
const alerts = useAlertsPage({
  filters: alertApplied,
  cursor: alertCursor,
  asOf: alertAsOf,
  limit: alertLimit,
})
const alertRows = computed(() => alerts.data.value?.items ?? [])
function nextAlertPage() {
  nextAlert({
    next_cursor: alerts.data.value?.next_cursor,
    as_of: alerts.data.value?.as_of,
    has_more: alerts.data.value?.has_more,
  })
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

const alertSearch: SearchField[] = [
  {
    field: 'severity', label: '级别', type: 'select',
    options: ['INFO', 'WARNING', 'CRITICAL'].map((value) => ({ label: value, value })),
  },
]
</script>

<template>
  <PageShell class="v2-page" title="完整性" :loading="alerts.isLoading.value" sub-title="运行时 · 告警 · 工作流 · 外部调用">
    <DetailSection title="运行时">
      <p class="runtime">状态 {{ (rt.data.value as { status?: string } | null)?.status ?? '—' }}</p>
    </DetailSection>

    <el-tabs v-model="active">
      <el-tab-pane label="Alerts" name="alerts" />
      <el-tab-pane label="Workflows" name="workflows" />
      <el-tab-pane label="External Calls" name="external-calls" />
    </el-tabs>

    <KeysetTable
      v-if="active === 'alerts'"
      :rows="alertRows"
      :loading="alerts.isLoading.value"
      :error="alerts.displayError.value"
      :denied="alerts.denied.value"
      :has-more="alerts.data.value?.has_more ?? false"
      :as-of="alerts.data.value?.as_of"
      :applied-filters="alertApplied"
      :search-fields="alertSearch"
      :page="alertPage"
      :page-size="alertLimit"
      :can-prev="alertCanPrev"
      @search="applyAlertFilters"
      @reset="resetAlertFilters"
      @refresh="alerts.refetch"
      @retry="alerts.refetch"
      @next="nextAlertPage"
      @prev="prevAlert"
      @size-change="setAlertLimit"
    >
      <el-table-column label="告警键" prop="alert_key" min-width="180" />
      <el-table-column label="级别" prop="severity" min-width="110" />
      <el-table-column label="代码" prop="code" min-width="140" />
      <el-table-column label="摘要" prop="message_redacted" min-width="240" />
      <el-table-column label="ID" prop="id" min-width="90" />
    </KeysetTable>

    <template v-else>
      <div class="crud-search">
        <el-form inline @submit.prevent="lookup">
          <el-form-item label="聚合类型">
            <el-select v-model="lookupType" aria-label="aggregate type" style="width: 200px">
              <el-option
                v-for="value in ['episode','decision','intent','chain_operation','forecast_submission','evidence_bundle']"
                :key="value" :label="value" :value="value"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="ID">
            <el-input v-model="lookupId" clearable placeholder="aggregate id" style="width: 220px" @keyup.enter="lookup" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :disabled="!lookupId.trim()" @click="lookup">查询链</el-button>
          </el-form-item>
        </el-form>
      </div>
      <p v-if="!lookupEnabled" class="hint">输入聚合类型与 ID，读取同一条工作流 / 外部调用审计链。</p>
      <PageState
        v-else
        :loading="workflow.isLoading.value"
        :error="workflow.displayError.value"
        :denied="workflow.denied.value"
        :empty="!workflow.isLoading.value && !workflow.isError.value && !chainRows.length"
        @retry="() => workflow.refetch()"
      >
        <DetailSection :title="active === 'workflows' ? '工作流事件' : '外部调用'">
          <el-table :data="chainRows" stripe>
            <el-table-column label="记录" min-width="400">
              <template #default="{ row }">{{ JSON.stringify(row) }}</template>
            </el-table-column>
          </el-table>
        </DetailSection>
      </PageState>
    </template>
  </PageShell>
</template>

<style scoped>
.runtime { margin: 0; color: var(--text-secondary); font-size: var(--text-sm); }
.crud-search {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: var(--space-base);
  margin-bottom: var(--space-base);
}
.hint { color: var(--text-secondary); font-size: var(--text-xs); }
</style>
