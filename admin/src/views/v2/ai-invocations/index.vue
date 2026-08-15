<script setup lang="ts">
import { computed } from 'vue'
import PageShell from '@/components/PageShell/index.vue'
import { KeysetTable, useKeysetList } from '../_shared'
import { useAiPage } from '@/queries/v2/ai'
import type { SearchField } from '@/components/CrudTable/types'

const { applied, cursor, asOf, limit, page, canPrev, applyFilters, resetFilters, next, prev, setLimit } = useKeysetList()
const { data, isLoading, displayError, denied, refetch } = useAiPage({
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
    field: 'role', label: '角色', type: 'select',
    options: ['scorer', 'researcher', 'verifier', 'labeler'].map((value) => ({ label: value, value })),
  },
  {
    field: 'lifecycle_state', label: '生命周期', type: 'select',
    options: ['PLANNED', 'ACCEPTED', 'REJECTED', 'FAILED', 'TIMEOUT', 'CANCELLED']
      .map((value) => ({ label: value, value })),
  },
]
</script>

<template>
  <PageShell class="v2-page" title="AI 调用" sub-title="供应商 · 生命周期 · 成本">
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
      <el-table-column label="ID" min-width="90">
        <template #default="{ row }">
          <RouterLink
            class="lnk"
            :to="{ path: `/ai-invocations/${row.id}`, query: { occurred_at: row.occurred_at } }"
          >{{ row.id }}</RouterLink>
        </template>
      </el-table-column>
      <el-table-column label="发生时间" prop="occurred_at" min-width="170" />
      <el-table-column label="角色" prop="role" min-width="110" />
      <el-table-column label="生命周期" prop="lifecycle_state" min-width="120" />
      <el-table-column label="供应商 / 模型" min-width="180">
        <template #default="{ row }">{{ row.requested_provider }} · {{ row.returned_model ?? row.requested_model }}</template>
      </el-table-column>
      <el-table-column label="成本" prop="cost_estimated" min-width="110" />
    </KeysetTable>
  </PageShell>
</template>

<style scoped>
.lnk { color: var(--primary); text-decoration: none; }
.lnk:hover { text-decoration: underline; }
</style>
