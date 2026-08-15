<script setup lang="ts">
import { computed } from 'vue'
import PageShell from '@/components/PageShell/index.vue'
import { KeysetTable, useKeysetList } from '../_shared'
import { useComponentsPage } from '@/queries/v2/components'

const { applied, cursor, asOf, limit, page, canPrev, applyFilters, resetFilters, next, prev, setLimit } = useKeysetList()
const { data, isLoading, displayError, denied, refetch } = useComponentsPage({
  cursor: cursor, asOf: asOf, limit: limit,
})
const rows = computed(() => data.value?.items ?? [])
function nextPage() {
  next({
    next_cursor: data.value?.next_cursor,
    as_of: data.value?.as_of,
    has_more: data.value?.has_more,
  })
}
</script>

<template>
  <PageShell class="v2-page" title="组件" sub-title="合约组件 · 版本 · 成本预算">
    <KeysetTable
      :rows="rows"
      :loading="isLoading"
      :error="displayError"
      :denied="denied"
      :has-more="data?.has_more ?? false"
      :as-of="data?.as_of"
      :applied-filters="applied"
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
      <el-table-column label="组件" min-width="160">
        <template #default="{ row }">
          <RouterLink class="lnk" :to="`/components/${row.id}`">{{ row.component_key }}</RouterLink>
        </template>
      </el-table-column>
      <el-table-column label="成本预算" prop="cost_budget" min-width="120" />
      <el-table-column label="说明" prop="description" min-width="240" />
      <el-table-column label="ID" prop="id" min-width="90" />
    </KeysetTable>
  </PageShell>
</template>

<style scoped>
.lnk { color: var(--primary); text-decoration: none; }
.lnk:hover { text-decoration: underline; }
</style>
