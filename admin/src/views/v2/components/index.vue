<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import PageShell from '@/components/PageShell/index.vue'
import { PageState } from '../_shared'
import { useComponentsPage } from '@/queries/v2/components'

const filters = ref<Record<string, never>>({})
const cursor = ref<string | null>(null)
const asOf = ref<string | null>(null)
const limit = ref(50)
const { data, isLoading, isError, displayError, denied, refetch } = useComponentsPage({
  filters: filters, cursor: cursor, asOf: asOf, limit: limit,
})
watch(filters, () => { cursor.value = null; asOf.value = null }, { deep: true })
const rows = computed(() => data.value?.items ?? [])
const hasMore = computed(() => data.value?.has_more ?? false)
function nextPage() { cursor.value = data.value?.next_cursor ?? null; asOf.value = data.value?.as_of ?? null }
</script>
<template>
  <PageShell class="v2-page" title="Components" :loading="isLoading" sub-title="组件 · 版本 · 成员合约">
    <PageState
:loading="isLoading"
:error="displayError" :denied="denied" :empty="!isLoading && !isError && !rows.length"
      @retry="() => refetch()">
      <el-table v-loading="isLoading" :data="rows" stripe>
        <el-table-column label="component_key" min-width="140"><template #default="{ row }">
          <RouterLink class="lnk" :to="`/v2/components/${row.id}`">{{ row.component_key }}</RouterLink>
        </template></el-table-column>
        <el-table-column label="cost_budget" prop="cost_budget" min-width="110">
          <template #default="{ row }"><span class="mono">{{ row.cost_budget }}</span></template>
        </el-table-column>
        <el-table-column label="description" prop="description" min-width="220" />
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
.pager{display:flex;justify-content:space-between;align-items:center;margin-top:var(--v2-space-3)}
.link-btn{background:none;border:none;color:var(--v2-primary);text-decoration:underline;cursor:pointer;height:var(--v2-control-h)}
.link-btn:disabled{color:var(--v2-ink-muted);cursor:not-allowed;text-decoration:none}
.muted{color:var(--v2-ink-muted);font-size:12.5px}
.mono{font-family:var(--v2-font-mono);font-size:12px}
.lnk{color:var(--v2-primary);text-decoration:underline}
</style>
