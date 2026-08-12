<script setup lang="ts">
/** WP-07B Execution：Intents/Orders/Positions/Ledger 四个 keyset tab。 */
import { ref, watch, computed } from 'vue'
import PageShell from '@/components/PageShell/index.vue'
import { PageState, StatusBadge } from '../_shared'
import { useIntentsPage, useOrdersPage, usePositionsPage, useLedgerPage } from '@/queries/v2/execution'

const active = ref('intents')
// Intents
const it_f = ref<Record<string,string>>({}); const it_c = ref<string|null>(null); const it_a = ref<string|null>(null)
const it = useIntentsPage({ filters: it_f.value, cursor: it_c.value, asOf: it_a.value, limit: 50 })
watch(it_f, () => { it_c.value = null; it_a.value = null }, { deep: true })
// Orders
const od_f = ref<Record<string,string>>({}); const od_c = ref<string|null>(null); const od_a = ref<string|null>(null)
const od = useOrdersPage({ filters: od_f.value, cursor: od_c.value, asOf: od_a.value, limit: 50 })
watch(od_f, () => { od_c.value = null; od_a.value = null }, { deep: true })
// Positions
const po_c = ref<string|null>(null)
const po = usePositionsPage({ filters: {}, cursor: po_c.value, asOf: null, limit: 50 })
// Ledger
const ld_f = ref<Record<string,string>>({}); const ld_c = ref<string|null>(null); const ld_a = ref<string|null>(null)
const ld = useLedgerPage({ filters: ld_f.value, cursor: ld_c.value, asOf: ld_a.value, limit: 50 })
watch(ld_f, () => { ld_c.value = null; ld_a.value = null }, { deep: true })
const rows = computed<unknown[]>(() => ({
  intents: it.data.value?.items ?? [], orders: od.data.value?.items ?? [],
  positions: po.data.value?.items ?? [], ledger: ld.data.value?.items ?? []
} as Record<string, unknown[]>)[active.value])
const loading = computed<boolean>(() => !!({ intents: it.isLoading.value, orders: od.isLoading.value,
  positions: po.isLoading.value, ledger: ld.isLoading.value })[active.value])
const hasMore = computed(() => ({ intents: it.data.value?.has_more, orders: od.data.value?.has_more,
  positions: po.data.value?.has_more, ledger: ld.data.value?.has_more })[active.value] ?? false)
const err = computed<string | null>(() => ({ intents: it.isError.value ? String(it.error.value) : null,
  orders: od.isError.value ? String(od.error.value) : null,
  positions: po.isError.value ? String(po.error.value) : null,
  ledger: ld.isError.value ? String(ld.error.value) : null } as Record<string, string | null>)[active.value])
const asOf = computed(() => ({ intents: it.data.value?.as_of, orders: od.data.value?.as_of,
  positions: po.data.value?.as_of, ledger: ld.data.value?.as_of })[active.value])
function next() {
  const n = { intents: it.data.value?.next_cursor, orders: od.data.value?.next_cursor,
    positions: po.data.value?.next_cursor, ledger: ld.data.value?.next_cursor }[active.value]
  if (active.value === 'positions') po_c.value = n ?? null
  else if (active.value === 'intents') it_c.value = n ?? null
  else if (active.value === 'orders') od_c.value = n ?? null
  else ld_c.value = n ?? null
}
</script>
<template>
  <PageShell title="Execution" :loading="loading" sub-title="intents · orders · positions · ledger">
    <el-tabs v-model="active" class="v2-tabs">
      <el-tab-pane label="Intents" name="intents" />
      <el-tab-pane label="Orders" name="orders" />
      <el-tab-pane label="Positions" name="positions" />
      <el-tab-pane label="Ledger" name="ledger" />
    </el-tabs>
    <PageState :loading="loading" :error="err" :denied="false" :empty="!loading && !err && !rows.length">
      <el-table v-loading="loading" :data="rows" stripe>
        <el-table-column label="key" min-width="180"><template #default="{ row }"><span class="mono">{{ row.id }}</span></template></el-table-column>
        <el-table-column label="status" min-width="110"><template #default="{ row }">
          <StatusBadge :tone="row.status === 'FILLED' || row.status === 'POSTED' ? 'success' : 'info'">{{ row.status }}</StatusBadge>
        </template></el-table-column>
        <el-table-column label="ref" min-width="200"><template #default="{ row }"><span class="mono">{{ row.order_key || row.transaction_key || row.intent_key || '' }}</span></template></el-table-column>
        <el-table-column label="created_at" prop="created_at" min-width="160"><template #default="{ row }"><span class="mono">{{ row.created_at }}</span></template></el-table-column>
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
