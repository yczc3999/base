<script setup lang="ts">
/** WP-07B Evaluation：Labels/Metrics/Promotions 三个 keyset tab。 */
import { computed, ref } from 'vue'
import PageShell from '@/components/PageShell/index.vue'
import { KeysetTable, useKeysetList } from '../_shared'
import { useLabelsPage, useMetricsPage, usePromotionsPage } from '@/queries/v2/evaluation'
import type { SearchField } from '@/components/CrudTable/types'

const active = ref('labels')
const labels = useKeysetList()
const metrics = useKeysetList()
const promotions = useKeysetList()

const lb = useLabelsPage({
  filters: labels.applied, cursor: labels.cursor, asOf: labels.asOf, limit: labels.limit,
})
const mt = useMetricsPage({
  filters: metrics.applied, cursor: metrics.cursor, asOf: metrics.asOf, limit: metrics.limit,
})
const pm = usePromotionsPage({
  filters: promotions.applied, cursor: promotions.cursor, asOf: promotions.asOf, limit: promotions.limit,
})

const current = computed(() => {
  const pack = {
    labels: { query: lb, list: labels },
    metrics: { query: mt, list: metrics },
    promotions: { query: pm, list: promotions },
  }[active.value]!
  return {
    applied: pack.list.applied.value,
    page: pack.list.page.value,
    limit: pack.list.limit.value,
    canPrev: pack.list.canPrev.value,
    applyFilters: pack.list.applyFilters,
    resetFilters: pack.list.resetFilters,
    prev: pack.list.prev,
    setLimit: pack.list.setLimit,
    next: pack.list.next,
    refetch: pack.query.refetch,
    isLoading: pack.query.isLoading.value,
    displayError: pack.query.displayError.value,
    denied: pack.query.denied.value,
    data: pack.query.data.value,
  }
})

const rows = computed(() => current.value.data?.items ?? [])
function nextPage() {
  current.value.next({
    next_cursor: current.value.data?.next_cursor,
    as_of: current.value.data?.as_of,
    has_more: current.value.data?.has_more,
  })
}

const searchFields = computed<SearchField[]>(() => {
  if (active.value === 'labels') return [{ field: 'state', label: '状态', type: 'input', placeholder: 'label 状态' }]
  if (active.value === 'metrics') return [{ field: 'status', label: '状态', type: 'input', placeholder: 'metric 状态' }]
  return [{ field: 'status', label: '状态', type: 'input', placeholder: 'promotion 状态' }]
})
</script>

<template>
  <PageShell class="v2-page" title="评估" sub-title="标签 · 指标 · 晋级">
    <el-tabs v-model="active">
      <el-tab-pane label="标签" name="labels" />
      <el-tab-pane label="指标" name="metrics" />
      <el-tab-pane label="晋级" name="promotions" />
    </el-tabs>
    <KeysetTable
      :rows="rows"
      :loading="current.isLoading"
      :error="current.displayError"
      :denied="current.denied"
      :has-more="current.data?.has_more ?? false"
      :as-of="current.data?.as_of"
      :applied-filters="current.applied"
      :search-fields="searchFields"
      :page="current.page"
      :page-size="current.limit"
      :can-prev="current.canPrev"
      @search="current.applyFilters"
      @reset="current.resetFilters"
      @refresh="current.refetch"
      @retry="current.refetch"
      @next="nextPage"
      @prev="current.prev"
      @size-change="current.setLimit"
    >
      <el-table-column label="键" min-width="200">
        <template #default="{ row }">{{ row.label_key || row.run_key || row.promotion_key }}</template>
      </el-table-column>
      <el-table-column label="状态" min-width="140">
        <template #default="{ row }">{{ row.state || row.status }}</template>
      </el-table-column>
      <el-table-column label="ID" prop="id" min-width="90" />
    </KeysetTable>
  </PageShell>
</template>
