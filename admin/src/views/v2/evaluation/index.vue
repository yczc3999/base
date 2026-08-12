<script setup lang="ts">
/** WP-07B Evaluation：Labels/Metrics/Promotions 三个 keyset tab。 */
import { ref, watch, computed } from 'vue'
import PageShell from '@/components/PageShell/index.vue'
import { PageState, StatusBadge } from '../_shared'
import { useLabelsPage, useMetricsPage, usePromotionsPage } from '@/queries/v2/evaluation'

const active = ref('labels')
const lb_f = ref<Record<string,string>>({}); const lb_c = ref<string|null>(null); const lb_a = ref<string|null>(null)
const lb = useLabelsPage({ filters: lb_f.value, cursor: lb_c.value, asOf: lb_a.value, limit: 50 })
watch(lb_f, () => { lb_c.value = null; lb_a.value = null }, { deep: true })
const mt_f = ref<Record<string,string>>({}); const mt_c = ref<string|null>(null); const mt_a = ref<string|null>(null)
const mt = useMetricsPage({ filters: mt_f.value, cursor: mt_c.value, asOf: mt_a.value, limit: 50 })
watch(mt_f, () => { mt_c.value = null; mt_a.value = null }, { deep: true })
const pm_f = ref<Record<string,string>>({}); const pm_c = ref<string|null>(null); const pm_a = ref<string|null>(null)
const pm = usePromotionsPage({ filters: pm_f.value, cursor: pm_c.value, asOf: pm_a.value, limit: 50 })
watch(pm_f, () => { pm_c.value = null; pm_a.value = null }, { deep: true })
const rows = computed<unknown[]>(() => ({
  labels: lb.data.value?.items ?? [], metrics: mt.data.value?.items ?? [], promotions: pm.data.value?.items ?? []
} as Record<string, unknown[]>)[active.value])
const loading = computed<boolean>(() => !!({ labels: lb.isLoading.value, metrics: mt.isLoading.value,
  promotions: pm.isLoading.value })[active.value])
const hasMore = computed(() => ({ labels: lb.data.value?.has_more, metrics: mt.data.value?.has_more,
  promotions: pm.data.value?.has_more })[active.value] ?? false)
const err = computed<string | null>(() => ({ labels: lb.isError.value ? String(lb.error.value) : null,
  metrics: mt.isError.value ? String(mt.error.value) : null,
  promotions: pm.isError.value ? String(pm.error.value) : null } as Record<string, string | null>)[active.value])
const asOf = computed(() => ({ labels: lb.data.value?.as_of, metrics: mt.data.value?.as_of,
  promotions: pm.data.value?.as_of })[active.value])
function next() {
  const n = { labels: lb.data.value?.next_cursor, metrics: mt.data.value?.next_cursor,
    promotions: pm.data.value?.next_cursor }[active.value]
  if (active.value === 'labels') lb_c.value = n ?? null
  else if (active.value === 'metrics') mt_c.value = n ?? null
  else pm_c.value = n ?? null
}
</script>
<template>
  <PageShell title="Evaluation" :loading="loading" sub-title="labels · metrics · promotions">
    <el-tabs v-model="active" class="v2-tabs">
      <el-tab-pane label="Labels" name="labels" />
      <el-tab-pane label="Metrics" name="metrics" />
      <el-tab-pane label="Promotions" name="promotions" />
    </el-tabs>
    <PageState :loading="loading" :error="err" :denied="false" :empty="!loading && !err && !rows.length">
      <el-table v-loading="loading" :data="rows" stripe>
        <el-table-column label="key" min-width="180"><template #default="{ row }"><span class="mono">{{ row.label_key || row.run_key || row.promotion_key }}</span></template></el-table-column>
        <el-table-column label="state" min-width="130"><template #default="{ row }">
          <StatusBadge :tone="row.status === 'APPROVED' || row.state === 'final_admissible' ? 'success' : 'info'">{{ row.state || row.status }}</StatusBadge>
        </template></el-table-column>
        <el-table-column label="id" prop="id" min-width="90"><template #default="{ row }"><span class="mono">{{ row.id }}</span></template></el-table-column>
      </el-table>
      <div class="pager">
        <span class="muted">{{ rows.length }} 条 · as_of {{ asOf }}</span>
        <button class="link-btn" :disabled="!hasMore || loading" @click="next">下一页 ›</button>
      </div>
    </PageState>
  </PageShell>
</template>
<style scoped>
.v2-tabs :deep(.el-tabs__item){color:var(--v2-ink-muted)}
.v2-tabs :deep(.el-tabs__item.is-active){color:var(--v2-primary)}
.pager{display:flex;justify-content:space-between;align-items:center;margin-top:var(--v2-space-3)}
.link-btn{background:none;border:none;color:var(--v2-primary);text-decoration:underline;cursor:pointer;height:var(--v2-control-h)}
.link-btn:disabled{color:var(--v2-ink-muted);cursor:not-allowed;text-decoration:none}
.muted{color:var(--v2-ink-muted);font-size:12.5px}
.mono{font-family:var(--v2-font-mono);font-size:12px}
</style>
