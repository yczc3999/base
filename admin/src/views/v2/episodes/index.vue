<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import PageShell from '@/components/PageShell/index.vue'
import { PageState, StatusBadge } from '../_shared'
import { useEpisodesPage } from '@/queries/v2/episodes'

const filters = ref<Record<string, string>>({})
const cursor = ref<string | null>(null)
const asOf = ref<string | null>(null)
const limit = ref(50)
const { data, isLoading, isError, displayError, denied, refetch } = useEpisodesPage({
  filters: filters, cursor: cursor, asOf: asOf, limit: limit,
})
watch(filters, () => { cursor.value = null; asOf.value = null }, { deep: true })
const rows = computed(() => data.value?.items ?? [])
const hasMore = computed(() => data.value?.has_more ?? false)
function nextPage() { cursor.value = data.value?.next_cursor ?? null; asOf.value = data.value?.as_of ?? null }
</script>
<template>
  <PageShell class="v2-page" title="Episodes" :loading="isLoading" sub-title="认知 episode · 阶段 · 状态">
    <div class="filterbar">
      <el-select v-model="filters.status" placeholder="状态" clearable>
        <el-option v-for="s in ['DRAFT','ROUTED','BLIND_COMMITTED','REVEALED','DECIDED','PRE_COMMIT_TERMINAL']" :key="s" :label="s" :value="s" />
      </el-select>
    </div>
    <PageState
:loading="isLoading"
:error="displayError" :denied="denied" :empty="!isLoading && !isError && !rows.length"
      @retry="() => refetch()">
      <el-table v-loading="isLoading" :data="rows" stripe>
        <el-table-column label="episode_key" min-width="200"><template #default="{ row }">
          <RouterLink class="lnk" :to="`/v2/episodes/${row.id}`"><span class="mono">{{ row.episode_key }}</span></RouterLink>
        </template></el-table-column>
        <el-table-column label="status" min-width="120"><template #default="{ row }">
          <StatusBadge :tone="row.status === 'DECIDED' ? 'success' : 'info'">{{ row.status }}</StatusBadge>
        </template></el-table-column>
        <el-table-column label="cognition" prop="cognition_status" min-width="130" />
        <el-table-column label="cutoff_at" prop="cutoff_at" min-width="160"><template #default="{ row }"><span class="mono">{{ row.cutoff_at }}</span></template></el-table-column>
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
.filterbar .el-select{width:180px}
.lnk{color:var(--v2-primary);text-decoration:underline}
.pager{display:flex;justify-content:space-between;align-items:center;margin-top:var(--v2-space-3)}
.link-btn{background:none;border:none;color:var(--v2-primary);text-decoration:underline;cursor:pointer;height:var(--v2-control-h)}
.link-btn:disabled{color:var(--v2-ink-muted);cursor:not-allowed;text-decoration:none}
.muted{color:var(--v2-ink-muted);font-size:12.5px}
.mono{font-family:var(--v2-font-mono);font-size:12px}
</style>
