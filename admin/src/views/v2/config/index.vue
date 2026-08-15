<script setup lang="ts">
import { computed } from 'vue'
import PageShell from '@/components/PageShell/index.vue'
import { ArtifactLink, KeysetTable, useKeysetList } from '../_shared'
import { useConfigurationPage } from '@/queries/v2/configuration'
import type { SearchField } from '@/components/CrudTable/types'

const { applied, cursor, asOf, limit, page, canPrev, applyFilters, resetFilters, next, prev, setLimit } = useKeysetList()
const { data, isLoading, displayError, denied, refetch } = useConfigurationPage({
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
  <PageShell class="v2-page" title="策略配置" sub-title="配置版本 · 只读">
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
      <el-table-column label="配置键" prop="config_key" min-width="160" />
      <el-table-column label="版本" prop="version_no" width="90" />
      <el-table-column label="内容哈希" min-width="180">
        <template #default="{ row }"><ArtifactLink :content-hash="row.content_hash" /></template>
      </el-table-column>
      <el-table-column label="状态" prop="status" min-width="110" />
      <el-table-column label="ID" prop="id" min-width="90" />
    </KeysetTable>
  </PageShell>
</template>
