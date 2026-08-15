<script setup lang="ts">
import { computed } from 'vue'
import PageShell from '@/components/PageShell/index.vue'
import { ArtifactLink, KeysetTable, useKeysetList } from '../_shared'
import { useReplayPage } from '@/queries/v2/replay'

const { applied, cursor, asOf, limit, page, canPrev, applyFilters, resetFilters, next, prev, setLimit } = useKeysetList()
const { data, isLoading, displayError, denied, refetch } = useReplayPage({
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
  <PageShell class="v2-page" title="回放" sub-title="确定性回放运行">
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
      <el-table-column label="运行键" prop="run_key" min-width="160" />
      <el-table-column label="类型" prop="replay_kind" min-width="120" />
      <el-table-column label="清单哈希" min-width="180">
        <template #default="{ row }"><ArtifactLink :content-hash="row.manifest_hash" /></template>
      </el-table-column>
      <el-table-column label="ID" prop="id" min-width="90" />
    </KeysetTable>
  </PageShell>
</template>
