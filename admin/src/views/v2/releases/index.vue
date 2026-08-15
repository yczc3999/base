<script setup lang="ts">
import { computed } from 'vue'
import PageShell from '@/components/PageShell/index.vue'
import { KeysetTable, useKeysetList } from '../_shared'
import { useReleasesPage } from '@/queries/v2/releases'
import type { SearchField } from '@/components/CrudTable/types'

const { applied, cursor, asOf, limit, page, canPrev, applyFilters, resetFilters, next, prev, setLimit } = useKeysetList()
const { data, isLoading, displayError, denied, refetch } = useReleasesPage({
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
  { field: 'status', label: '状态', type: 'input', placeholder: '请输入状态' },
]
</script>

<template>
  <PageShell class="v2-page" title="发布" sub-title="发布单 · 哈希 · 数据库版本">
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
      <el-table-column label="发布名" prop="release_name" min-width="160" />
      <el-table-column label="Git SHA" prop="git_sha" min-width="160" />
      <el-table-column label="库版本" prop="db_revision" min-width="120" />
      <el-table-column label="状态" prop="status" min-width="90" />
      <el-table-column label="ID" prop="id" min-width="90" />
    </KeysetTable>
  </PageShell>
</template>
