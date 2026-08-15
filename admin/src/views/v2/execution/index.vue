<script setup lang="ts">
/** WP-07B Execution：Intents/Orders/Positions/Ledger 四个 keyset tab。 */
import { computed, ref } from 'vue'
import PageShell from '@/components/PageShell/index.vue'
import { KeysetTable, useKeysetList } from '../_shared'
import { useIntentsPage, useOrdersPage, usePositionsPage, useLedgerPage } from '@/queries/v2/execution'
import type { SearchField } from '@/components/CrudTable/types'

const active = ref('intents')
const intents = useKeysetList()
const orders = useKeysetList()
const positions = useKeysetList()
const ledger = useKeysetList()

const it = useIntentsPage({
  filters: intents.applied, cursor: intents.cursor, asOf: intents.asOf, limit: intents.limit,
})
const od = useOrdersPage({
  filters: orders.applied, cursor: orders.cursor, asOf: orders.asOf, limit: orders.limit,
})
const po = usePositionsPage({
  cursor: positions.cursor, asOf: positions.asOf, limit: positions.limit,
})
const ld = useLedgerPage({
  filters: ledger.applied, cursor: ledger.cursor, asOf: ledger.asOf, limit: ledger.limit,
})

const current = computed(() => {
  const pack = {
    intents: { query: it, list: intents },
    orders: { query: od, list: orders },
    positions: { query: po, list: positions },
    ledger: { query: ld, list: ledger },
  }[active.value]!
  return {
    applied: pack.list.applied.value,
    page: pack.list.page.value,
    limit: pack.list.limit.value,
    canPrev: pack.list.canPrev.value,
    applyFilters: pack.list.applyFilters,
    resetFilters: pack.list.resetFilters,
    prev: pack.list.prev,
    setLimit: pack.list.setLimit,
    next: pack.list.next,
    refetch: pack.query.refetch,
    isLoading: pack.query.isLoading.value,
    displayError: pack.query.displayError.value,
    denied: pack.query.denied.value,
    data: pack.query.data.value,
  }
})

const rows = computed(() => current.value.data?.items ?? [])
function nextPage() {
  current.value.next({
    next_cursor: current.value.data?.next_cursor,
    as_of: current.value.data?.as_of,
    has_more: current.value.data?.has_more,
  })
}

const searchFields = computed<SearchField[]>(() => {
  if (active.value === 'intents') return [{ field: 'status', label: '状态', type: 'input', placeholder: 'intent 状态' }]
  if (active.value === 'orders') return [{ field: 'status', label: '状态', type: 'input', placeholder: 'order 状态' }]
  if (active.value === 'ledger') return [{ field: 'kind', label: '类型', type: 'input', placeholder: 'ledger 类型' }]
  return []
})
</script>

<template>
  <PageShell class="v2-page" title="执行" sub-title="意图 · 订单 · 持仓 · 账本">
    <el-tabs v-model="active">
      <el-tab-pane label="意图" name="intents" />
      <el-tab-pane label="订单" name="orders" />
      <el-tab-pane label="持仓" name="positions" />
      <el-tab-pane label="账本" name="ledger" />
    </el-tabs>
    <KeysetTable
      :rows="rows"
      :loading="current.isLoading"
      :error="current.displayError"
      :denied="current.denied"
      :has-more="current.data?.has_more ?? false"
      :as-of="current.data?.as_of"
      :applied-filters="current.applied"
      :search-fields="searchFields"
      :page="current.page"
      :page-size="current.limit"
      :can-prev="current.canPrev"
      @search="current.applyFilters"
      @reset="current.resetFilters"
      @refresh="current.refetch"
      @retry="current.refetch"
      @next="nextPage"
      @prev="current.prev"
      @size-change="current.setLimit"
    >
      <el-table-column label="ID" min-width="160">
        <template #default="{ row }">{{ row.id }}</template>
      </el-table-column>
      <el-table-column label="状态" min-width="110">
        <template #default="{ row }">{{ row.status }}</template>
      </el-table-column>
      <el-table-column label="引用" min-width="200">
        <template #default="{ row }">{{ row.order_key || row.transaction_key || row.intent_key || '' }}</template>
      </el-table-column>
      <el-table-column label="创建时间" prop="created_at" min-width="170" />
    </KeysetTable>
  </PageShell>
</template>
