<script setup lang="ts">
/** WP-07B Integrity：runtime 快照 + Alerts keyset。 */
import { ref, watch, computed } from 'vue'
import PageShell from '@/components/PageShell/index.vue'
import { PageState, StatusBadge } from '../_shared'
import { useAlertsPage, useIntegrityRuntime } from '@/queries/v2/integrity'

const rt = useIntegrityRuntime()
const al_f = ref<Record<string,string>>({}); const al_c = ref<string|null>(null); const al_a = ref<string|null>(null)
const al = useAlertsPage({ filters: al_f.value, cursor: al_c.value, asOf: al_a.value, limit: 50 })
watch(al_f, () => { al_c.value = null; al_a.value = null }, { deep: true })
const rows = computed(() => al.data.value?.items ?? [])
const hasMore = computed(() => al.data.value?.has_more ?? false)
function next() { al_c.value = al.data.value?.next_cursor ?? null; al_a.value = al.data.value?.as_of ?? null }
</script>
<template>
  <PageShell title="Integrity" :loading="al.isLoading.value" sub-title="runtime · alerts">
    <div class="section">
      <h2>Runtime</h2>
      <p class="mono">{{ JSON.stringify(rt.data.value ?? null) }}</p>
    </div>
    <PageState
:loading="al.isLoading.value" :error="al.isError.value ? String(al.error.value) : null"
      :denied="false" :empty="!al.isLoading.value && !al.isError.value && !rows.length">
      <el-table v-loading="al.isLoading.value" :data="rows" stripe>
        <el-table-column label="alert_key" prop="alert_key" min-width="180" />
        <el-table-column label="severity" min-width="110"><template #default="{ row }">
          <StatusBadge :tone="row.severity === 'CRITICAL' ? 'danger' : row.severity === 'WARNING' ? 'warning' : 'info'">{{ row.severity }}</StatusBadge>
        </template></el-table-column>
        <el-table-column label="code" prop="code" min-width="140" />
        <el-table-column label="message" prop="message_redacted" min-width="220" />
        <el-table-column label="id" prop="id" min-width="90"><template #default="{ row }"><span class="mono">{{ row.id }}</span></template></el-table-column>
      </el-table>
      <div class="pager">
        <span class="muted">{{ rows.length }} 条 · as_of {{ al.data.value?.as_of }}</span>
        <button class="link-btn" :disabled="!hasMore || al.isLoading.value" @click="next">下一页 ›</button>
      </div>
    </PageState>
  </PageShell>
</template>
<style scoped>
.section{background:var(--v2-surface);border:1px solid var(--v2-line);border-radius:var(--v2-radius-md);margin-bottom:var(--v2-space-4);padding:var(--v2-space-4)}
.section h2{font-size:15px;font-weight:700;margin-bottom:var(--v2-space-2)}
.mono{font-family:var(--v2-font-mono);font-size:12px;word-break:break-all}
.pager{display:flex;justify-content:space-between;align-items:center;margin-top:var(--v2-space-3)}
.link-btn{background:none;border:none;color:var(--v2-primary);text-decoration:underline;cursor:pointer;height:var(--v2-control-h)}
.link-btn:disabled{color:var(--v2-ink-muted);cursor:not-allowed;text-decoration:none}
</style>
