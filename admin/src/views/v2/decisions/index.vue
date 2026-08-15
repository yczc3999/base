<script setup lang="ts">
import { computed } from 'vue'
import PageShell from '@/components/PageShell/index.vue'
import { KeysetTable, useKeysetList } from '../_shared'
import { useDecisionsPage } from '@/queries/v2/decisions'
import type { SearchField } from '@/components/CrudTable/types'

const { applied, cursor, asOf, limit, page, canPrev, applyFilters, resetFilters, next, prev, setLimit } = useKeysetList()
const { data, isLoading, displayError, denied, refetch } = useDecisionsPage({
  filters: applied, cursor: cursor, asOf: asOf, limit: limit,
})
const rows = computed(() => data.value?.items ?? [])
function nextPage() {
  next({
    next_cursor: data.value?.next_cursor,
    as_of: data.value?.as_of,
    has_more: data.value?.has_more,
  })
}

const searchFields: SearchField[] = [
  {
    field: 'decision_class', label: '类别', type: 'select',
    options: ['CHAMPION', 'RISK_REVIEW'].map((value) => ({ label: value, value })),
  },
  {
    field: 'status', label: '状态', type: 'select',
    options: ['CREATED', 'QUOTE_BOUND', 'G7A', 'G7B', 'ACTION', 'WAIT', 'ABSTAIN']
      .map((value) => ({ label: value, value })),
  },
]
</script>

<template>
  <PageShell class="v2-page" title="决策" sub-title="动作 · 状态 · 触发时间">
    <KeysetTable
      :rows="rows"
      :loading="isLoading"
      :error="displayError"
      :denied="denied"
      :has-more="data?.has_more ?? false"
      :as-of="data?.as_of"
      :applied-filters="applied"
      :search-fields="searchFields"
      :page="page"
      :page-size="limit"
      :can-prev="canPrev"
      @search="applyFilters"
      @reset="resetFilters"
      @refresh="refetch"
      @retry="refetch"
      @next="nextPage"
      @prev="prev"
      @size-change="setLimit"
    >
      <el-table-column label="决策" min-width="220">
        <template #default="{ row }">
          <RouterLink class="lnk" :to="`/decisions/${row.id}`">{{ row.decision_key }}</RouterLink>
        </template>
      </el-table-column>
      <el-table-column label="类别" prop="decision_class" min-width="120" />
      <el-table-column label="状态" prop="status" min-width="110" />
      <el-table-column label="动作" prop="selected_action_type" min-width="170" />
      <el-table-column label="触发时间" prop="trigger_at" min-width="170" />
      <el-table-column label="ID" prop="id" min-width="90" />
    </KeysetTable>
  </PageShell>
</template>

<style scoped>
.lnk { color: var(--primary); text-decoration: none; }
.lnk:hover { text-decoration: underline; }
</style>
