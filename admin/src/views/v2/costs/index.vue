<script setup lang="ts">
import { computed } from 'vue'
import PageShell from '@/components/PageShell/index.vue'
import { KeysetTable, useKeysetList } from '../_shared'
import { useCostsPage } from '@/queries/v2/costs'
import type { SearchField } from '@/components/CrudTable/types'

const { applied, cursor, asOf, limit, page, canPrev, applyFilters, resetFilters, next, prev, setLimit } = useKeysetList()
const { data, isLoading, displayError, denied, refetch } = useCostsPage({
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
  { field: 'cost_kind', label: '类别', type: 'input', placeholder: '请输入类别' },
]
</script>

<template>
  <PageShell class="v2-page" title="费用" sub-title="成本 · 按类别">
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
      <el-table-column label="费用键" prop="cost_key" min-width="160" />
      <el-table-column label="类别" prop="cost_kind" min-width="120" />
      <el-table-column label="金额" prop="amount" min-width="110" />
      <el-table-column label="ID" prop="id" min-width="90" />
    </KeysetTable>
  </PageShell>
</template>
