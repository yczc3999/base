<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import PageShell from '@/components/PageShell/index.vue'
import { PageState, StatusBadge } from '../_shared'
import { useDecisionsPage } from '@/queries/v2/decisions'

const filters = ref<Record<string, string>>({})
const cursor = ref<string | null>(null)
const asOf = ref<string | null>(null)
const limit = ref(50)
const { data, isLoading, isError, displayError, denied, refetch } = useDecisionsPage({
  filters: filters, cursor: cursor, asOf: asOf, limit: limit,
})
watch(filters, () => { cursor.value = null; asOf.value = null }, { deep: true })
const rows = computed(() => data.value?.items ?? [])
const hasMore = computed(() => data.value?.has_more ?? false)
function nextPage() { cursor.value = data.value?.next_cursor ?? null; asOf.value = data.value?.as_of ?? null }
</script>
<template>
  <PageShell class="v2-page" title="Decisions" :loading="isLoading" sub-title="决策 · action · 状态">
    <div class="filterbar">
      <el-select v-model="filters.decision_class" placeholder="class" clearable style="width:160px">
        <el-option v-for="c in ['CHAMPION','RISK_REVIEW']" :key="c" :label="c" :value="c" />
      </el-select>
      <el-select v-model="filters.status" placeholder="状态" clearable style="width:160px">
        <el-option v-for="s in ['CREATED','QUOTE_BOUND','G7A','G7B','ACTION','WAIT','ABSTAIN']" :key="s" :label="s" :value="s" />
      </el-select>
    </div>
    <PageState
:loading="isLoading"
:error="displayError" :denied="denied" :empty="!isLoading && !isError && !rows.length"
      @retry="() => refetch()">
      <el-table v-loading="isLoading" :data="rows" stripe>
        <el-table-column label="decision_key" min-width="200"><template #default="{ row }">
          <RouterLink class="lnk" :to="`/v2/decisions/${row.id}`"><span class="mono">{{ row.decision_key }}</span></RouterLink>
        </template></el-table-column>
        <el-table-column label="class" prop="decision_class" min-width="120" />
        <el-table-column label="status" min-width="100"><template #default="{ row }">
          <StatusBadge :tone="row.status === 'ACTION' ? 'success' : 'info'">{{ row.status }}</StatusBadge>
        </template></el-table-column>
        <el-table-column label="action" prop="selected_action_type" min-width="170" />
        <el-table-column label="trigger_at" prop="trigger_at" min-width="160"><template #default="{ row }"><span class="mono">{{ row.trigger_at }}</span></template></el-table-column>
        <el-table-column label="id" prop="id" min-width="90"><template #default="{ row }"><span class="mono">{{ row.id }}</span></template></el-table-column>
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
.lnk{color:var(--v2-primary);text-decoration:underline}
.pager{display:flex;justify-content:space-between;align-items:center;margin-top:var(--v2-space-3)}
.link-btn{background:none;border:none;color:var(--v2-primary);text-decoration:underline;cursor:pointer;height:var(--v2-control-h)}
.link-btn:disabled{color:var(--v2-ink-muted);cursor:not-allowed;text-decoration:none}
.muted{color:var(--v2-ink-muted);font-size:12.5px}
.mono{font-family:var(--v2-font-mono);font-size:12px}
</style>
