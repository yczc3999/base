<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import PageShell from '@/components/PageShell/index.vue'
import { PageState, StatusBadge } from '../_shared'
import { useAiPage } from '@/queries/v2/ai'

const filters = ref<Record<string, string>>({})
const cursor = ref<string | null>(null)
const asOf = ref<string | null>(null)
const limit = ref(50)
const { data, isLoading, isError, error } = useAiPage({
  filters: filters.value, cursor: cursor.value, asOf: asOf.value, limit: limit.value,
})
watch(filters, () => { cursor.value = null; asOf.value = null }, { deep: true })
const rows = computed(() => data.value?.items ?? [])
const hasMore = computed(() => data.value?.has_more ?? false)
function nextPage() { cursor.value = data.value?.next_cursor ?? null; asOf.value = data.value?.as_of ?? null }
</script>
<template>
  <PageShell title="AI Invocations" :loading="isLoading" sub-title="provider · lifecycle · cost">
    <div class="filterbar">
      <el-select v-model="filters.role" placeholder="role" clearable style="width:150px">
        <el-option v-for="r in ['scorer','researcher','verifier','labeler']" :key="r" :label="r" :value="r" />
      </el-select>
      <el-select v-model="filters.lifecycle_state" placeholder="状态" clearable style="width:150px">
        <el-option v-for="s in ['PLANNED','ACCEPTED','REJECTED','FAILED','TIMEOUT','CANCELLED']" :key="s" :label="s" :value="s" />
      </el-select>
    </div>
    <PageState
:loading="isLoading" :error="isError ? String(error) : null" :denied="false"
      :empty="!isLoading && !isError && !rows.length">
      <el-table v-loading="isLoading" :data="rows" stripe>
        <el-table-column label="id" min-width="90"><template #default="{ row }">
          <RouterLink class="lnk" :to="`/v2/ai-invocations/${row.id}`"><span class="mono">{{ row.id }}</span></RouterLink>
        </template></el-table-column>
        <el-table-column label="occurred_at" prop="occurred_at" min-width="160"><template #default="{ row }"><span class="mono">{{ row.occurred_at }}</span></template></el-table-column>
        <el-table-column label="role" prop="role" min-width="100" />
        <el-table-column label="lifecycle" min-width="110"><template #default="{ row }">
          <StatusBadge :tone="row.lifecycle_state === 'ACCEPTED' ? 'success' : row.lifecycle_state === 'PLANNED' ? 'info' : 'warning'">{{ row.lifecycle_state }}</StatusBadge>
        </template></el-table-column>
        <el-table-column label="provider / model" min-width="160"><template #default="{ row }">{{ row.requested_provider }} · {{ row.returned_model ?? row.requested_model }}</template></el-table-column>
        <el-table-column label="cost" prop="cost_estimated" min-width="110"><template #default="{ row }"><span class="mono">{{ row.cost_estimated }}</span></template></el-table-column>
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
