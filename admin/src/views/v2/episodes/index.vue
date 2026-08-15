<script setup lang="ts">
import { computed } from 'vue'
import PageShell from '@/components/PageShell/index.vue'
import { KeysetTable, useKeysetList } from '../_shared'
import { useEpisodesPage } from '@/queries/v2/episodes'
import type { SearchField } from '@/components/CrudTable/types'

const { applied, cursor, asOf, limit, page, canPrev, applyFilters, resetFilters, next, prev, setLimit } = useKeysetList()
const { data, isLoading, displayError, denied, refetch } = useEpisodesPage({
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
    field: 'status', label: '状态', type: 'select',
    options: ['DRAFT', 'ROUTED', 'BLIND_COMMITTED', 'REVEALED', 'DECIDED', 'PRE_COMMIT_TERMINAL']
      .map((value) => ({ label: value, value })),
  },
]
</script>

<template>
  <PageShell class="v2-page" title="回合" sub-title="认知 episode · 阶段 · 截止时间">
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
      <el-table-column label="回合" min-width="220">
        <template #default="{ row }">
          <RouterLink class="lnk" :to="`/episodes/${row.id}`">{{ row.episode_key }}</RouterLink>
        </template>
      </el-table-column>
      <el-table-column label="状态" prop="status" min-width="140" />
      <el-table-column label="认知" prop="cognition_status" min-width="130" />
      <el-table-column label="截止时间" prop="cutoff_at" min-width="170" />
      <el-table-column label="ID" prop="id" min-width="90" />
    </KeysetTable>
  </PageShell>
</template>

<style scoped>
.lnk { color: var(--primary); text-decoration: none; }
.lnk:hover { text-decoration: underline; }
</style>
