<script setup lang="ts">
import { computed } from 'vue'
import PageShell from '@/components/PageShell/index.vue'
import { ArtifactLink, KeysetTable, useKeysetList } from '../_shared'
import { useModelsPage } from '@/queries/v2/models'

const { applied, cursor, asOf, limit, page, canPrev, applyFilters, resetFilters, next, prev, setLimit } = useKeysetList()
const { data, isLoading, displayError, denied, refetch } = useModelsPage({
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
  <PageShell class="v2-page" title="模型" sub-title="角色绑定 · 供应商 · 路由">
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
      <el-table-column label="角色" prop="role" min-width="110" />
      <el-table-column label="供应商" prop="provider" min-width="110" />
      <el-table-column label="路由" prop="route" min-width="110" />
      <el-table-column label="模型" prop="model_ref" min-width="160" />
      <el-table-column label="内容哈希" min-width="180">
        <template #default="{ row }"><ArtifactLink :content-hash="row.content_hash" /></template>
      </el-table-column>
      <el-table-column label="ID" prop="id" min-width="90" />
    </KeysetTable>
  </PageShell>
</template>
