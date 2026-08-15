<script setup lang="ts">
/** WP-07B Markets 列表：keyset 翻页 + negRisk/closed filter + 下钻 Market Detail。 */
import { computed } from 'vue'
import PageShell from '@/components/PageShell/index.vue'
import { KeysetTable, useKeysetList } from '../_shared'
import { useMarketsPage } from '@/queries/v2/markets'
import type { SearchField } from '@/components/CrudTable/types'

const { applied, cursor, asOf, limit, page, canPrev, applyFilters, resetFilters, next, prev, setLimit } = useKeysetList()
const { data, isLoading, displayError, denied, refetch } = useMarketsPage({
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
    field: 'closed', label: '状态', type: 'select',
    options: [
      { label: '开放', value: 'false' },
      { label: '已关闭', value: 'true' },
    ],
  },
  {
    field: 'neg_risk', label: 'Neg Risk', type: 'select',
    options: [
      { label: '否', value: 'false' },
      { label: '是', value: 'true' },
    ],
  },
]
</script>

<template>
  <PageShell class="v2-page" title="市场" sub-title="公共市场 · 状态 · 流动性">
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
      <template #empty>
        <p class="t">还没有市场</p>
        <p class="m">标签同步不会拉行情。需要引擎跑完一帧 Gamma（sense）之后这里才有行。</p>
      </template>
      <el-table-column label="问题" min-width="280">
        <template #default="{ row }">
          <RouterLink class="lnk" :to="`/markets/${row.id}`">{{ row.question }}</RouterLink>
        </template>
      </el-table-column>
      <el-table-column label="Slug" prop="slug" min-width="140" />
      <el-table-column label="状态" width="100" align="center">
        <template #default="{ row }">
          <span class="status-badge" :class="row.closed ? 'info' : 'success'">
            {{ row.closed ? '已关闭' : '开放' }}
          </span>
        </template>
      </el-table-column>
      <el-table-column label="Neg Risk" width="100" align="center">
        <template #default="{ row }">{{ row.neg_risk ? '是' : '否' }}</template>
      </el-table-column>
      <el-table-column label="成交量" prop="volume" min-width="110" />
      <el-table-column label="ID" prop="id" min-width="90" />
    </KeysetTable>
  </PageShell>
</template>

<style scoped>
.lnk { color: var(--primary); text-decoration: none; }
.lnk:hover { text-decoration: underline; }
.status-badge {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  font-size: var(--text-xs);
  font-weight: 500;
  border-radius: var(--radius-sm);
}
.status-badge.success { background: var(--success-bg); color: var(--success); }
.status-badge.info { background: var(--info-bg); color: var(--info); }
</style>
