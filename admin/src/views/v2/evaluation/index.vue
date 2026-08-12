<script setup lang="ts">
/** WP-07B Evaluation：Labels/Metrics/Promotions 三个 keyset tab。 */
import { ref, watch, computed } from 'vue'
import PageShell from '@/components/PageShell/index.vue'
import { PageState, StatusBadge } from '../_shared'
import { useLabelsPage, useMetricsPage, usePromotionsPage } from '@/queries/v2/evaluation'

const active = ref('labels')
const lb_f = ref<Record<string,string>>({}); const lb_c = ref<string|null>(null); const lb_a = ref<string|null>(null)
const lb = useLabelsPage({ filters: lb_f, cursor: lb_c, asOf: lb_a, limit: 50 })
watch(lb_f, () => { lb_c.value = null; lb_a.value = null }, { deep: true })
const mt_f = ref<Record<string,string>>({}); const mt_c = ref<string|null>(null); const mt_a = ref<string|null>(null)
const mt = useMetricsPage({ filters: mt_f, cursor: mt_c, asOf: mt_a, limit: 50 })
watch(mt_f, () => { mt_c.value = null; mt_a.value = null }, { deep: true })
const pm_f = ref<Record<string,string>>({}); const pm_c = ref<string|null>(null); const pm_a = ref<string|null>(null)
const pm = usePromotionsPage({ filters: pm_f, cursor: pm_c, asOf: pm_a, limit: 50 })
watch(pm_f, () => { pm_c.value = null; pm_a.value = null }, { deep: true })
const rows = computed<unknown[]>(() => ({
  labels: lb.data.value?.items ?? [], metrics: mt.data.value?.items ?? [], promotions: pm.data.value?.items ?? []
} as Record<string, unknown[]>)[active.value])
const loading = computed<boolean>(() => !!({ labels: lb.isLoading.value, metrics: mt.isLoading.value,
  promotions: pm.isLoading.value })[active.value])
const hasMore = computed(() => ({ labels: lb.data.value?.has_more, metrics: mt.data.value?.has_more,
  promotions: pm.data.value?.has_more })[active.value] ?? false)
const err = computed<string | null>(() => ({ labels: lb.displayError.value,
  metrics: mt.displayError.value,
  promotions: pm.displayError.value } as Record<string, string | null>)[active.value])
const denied = computed<boolean>(() => Boolean(({ labels: lb.denied.value, metrics: mt.denied.value,
  promotions: pm.denied.value })[active.value]))
const asOf = computed(() => ({ labels: lb.data.value?.as_of, metrics: mt.data.value?.as_of,
  promotions: pm.data.value?.as_of })[active.value])
function retry() {
  const query = { labels: lb, metrics: mt, promotions: pm }[active.value]
  void query?.refetch()
}
function next() {
  const n = { labels: lb.data.value?.next_cursor, metrics: mt.data.value?.next_cursor,
    promotions: pm.data.value?.next_cursor }[active.value]
  const a = { labels: lb.data.value?.as_of, metrics: mt.data.value?.as_of,
    promotions: pm.data.value?.as_of }[active.value]
  if (active.value === 'labels') { lb_c.value = n ?? null; lb_a.value = a ?? null }
  else if (active.value === 'metrics') { mt_c.value = n ?? null; mt_a.value = a ?? null }
  else { pm_c.value = n ?? null; pm_a.value = a ?? null }
}
</script>
<template>
  <PageShell class="v2-page" title="Evaluation" :loading="loading" sub-title="labels · metrics · promotions">
    <el-tabs v-model="active" class="v2-tabs">
      <el-tab-pane label="Labels" name="labels" />
      <el-tab-pane label="Metrics" name="metrics" />
      <el-tab-pane label="Promotions" name="promotions" />
    </el-tabs>
    <PageState :loading="loading" :error="err" :denied="denied" :empty="!loading && !err && !rows.length" @retry="retry">
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
