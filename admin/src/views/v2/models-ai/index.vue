<script setup lang="ts">
import { ref, computed } from 'vue'
import PageShell from '@/components/PageShell/index.vue'
import { PageState } from '../_shared'
import { useModelsPage } from '@/queries/v2/models'

const cursor = ref<string | null>(null)
const asOf = ref<string | null>(null)
const limit = ref(50)
const { data, isLoading, isError, error } = useModelsPage({ cursor: cursor.value, asOf: asOf.value, limit: limit.value })
const rows = computed(() => data.value?.items ?? [])
const hasMore = computed(() => data.value?.has_more ?? false)
function nextPage() { cursor.value = data.value?.next_cursor ?? null; asOf.value = data.value?.as_of ?? null }
</script>
<template>
  <PageShell title="Models & AI" :loading="isLoading" sub-title="模型路由绑定">
    <PageState
:loading="isLoading" :error="isError ? String(error) : null" :denied="false"
      :empty="!isLoading && !isError && !rows.length">
      <el-table v-loading="isLoading" :data="rows" stripe>
        <el-table-column label="role" min-width="110"><template #default="{ row }">{{ row.role }}</template></el-table-column>
        <el-table-column label="provider" min-width="100"><template #default="{ row }">{{ row.provider }}</template></el-table-column>
        <el-table-column label="route" min-width="110"><template #default="{ row }">{{ row.route }}</template></el-table-column>
        <el-table-column label="model_ref" min-width="160"><template #default="{ row }">{{ row.model_ref }}</template></el-table-column>
        <el-table-column label="content_hash" min-width="180"><template #default="{ row }"><span class="mono">{{ row.content_hash }}</span></template></el-table-column>
        <el-table-column label="id" min-width="90"><template #default="{ row }"><span class="mono">{{ row.id }}</span></template></el-table-column>
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
</style>
