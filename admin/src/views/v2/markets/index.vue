<script setup lang="ts">
/** WP-07B Markets 列表：keyset 翻页 + negRisk/closed filter + 下钻 Market Detail。 */
import { ref, watch, computed } from 'vue'
import PageShell from '@/components/PageShell/index.vue'
import { PageState, StatusBadge } from '../_shared'
import { useMarketsPage } from '@/queries/v2/markets'

const filters = ref<Record<string, string>>({})
const cursor = ref<string | null>(null)
const asOf = ref<string | null>(null)
const limit = ref(50)

const { data, isLoading, isError, displayError, denied, refetch } = useMarketsPage({
  filters: filters, cursor: cursor, asOf: asOf, limit: limit,
})
// filter 改变 → 清空 cursor/asOf（复用 WP-07A query key 语义）
watch(filters, () => { cursor.value = null; asOf.value = null }, { deep: true })

const rows = computed(() => data.value?.items ?? [])
const hasMore = computed(() => data.value?.has_more ?? false)
function nextPage() { cursor.value = data.value?.next_cursor ?? null; asOf.value = data.value?.as_of ?? null }
</script>
<template>
  <PageShell class="v2-page" title="Markets" :loading="isLoading" sub-title="公共市场 · 状态 · 流动性">
    <div class="filterbar">
      <RouterLink class="lnk" to="/v2/tags">已同步 Tags</RouterLink>
      <el-select v-model="filters.neg_risk" placeholder="negRisk" clearable >
        <el-option label="否" value="false" /><el-option label="是" value="true" />
      </el-select>
      <el-select v-model="filters.closed" placeholder="状态" clearable>
        <el-option label="开放" value="false" /><el-option label="已关闭" value="true" />
      </el-select>
    </div>
    <PageState
:loading="isLoading"
:error="displayError" :denied="denied" :empty="!isLoading && !isError && !rows.length"
      @retry="() => refetch()">
      <el-table v-loading="isLoading" :data="rows" stripe>
        <el-table-column label="question" min-width="260"><template #default="{ row }">
          <RouterLink class="lnk" :to="`/v2/markets/${row.id}`">{{ row.question }}</RouterLink>
        </template></el-table-column>
        <el-table-column label="slug" prop="slug" min-width="140" />
        <el-table-column label="状态" width="120"><template #default="{ row }">
          <StatusBadge :tone="row.closed ? 'info' : 'success'">{{ row.closed ? 'CLOSED' : 'OPEN' }}</StatusBadge>
        </template></el-table-column>
        <el-table-column label="negRisk" width="90"><template #default="{ row }">{{ row.neg_risk ? '是' : '否' }}</template></el-table-column>
        <el-table-column label="volume" prop="volume" min-width="110" class-name="mono" />
        <el-table-column label="id" prop="id" min-width="90" class-name="mono" />
      </el-table>
      <div class="pager">
        <span class="muted">{{ rows.length }} 条 · as_of {{ data?.as_of }}</span>
        <button class="link-btn" :disabled="!hasMore || isLoading" @click="nextPage">下一页 ›</button>
      </div>
    </PageState>
  </PageShell>
</template>
<style scoped>
.filterbar{display:flex;gap:var(--v2-space-2);margin-bottom:var(--v2-space-4);flex-wrap:wrap}
.filterbar .el-select{width:160px}
.lnk{color:var(--v2-primary);text-decoration:underline}
.pager{display:flex;justify-content:space-between;align-items:center;margin-top:var(--v2-space-3)}
.link-btn{background:none;border:none;color:var(--v2-primary);text-decoration:underline;cursor:pointer;height:var(--v2-control-h)}
.link-btn:disabled{color:var(--v2-ink-muted);cursor:not-allowed;text-decoration:none}
.muted{color:var(--v2-ink-muted);font-size:12.5px}
:deep(.mono){font-family:var(--v2-font-mono);font-size:12px}
</style>

