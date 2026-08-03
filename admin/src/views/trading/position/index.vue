<template>
  <div>
    <!-- 汇总卡片 -->
    <div class="stat-cards">
      <div class="stat-card open">
        <div class="stat-num">{{ openCount }}</div>
        <div class="stat-label">当前持仓</div>
      </div>
      <div class="stat-card amount">
        <div class="stat-num">${{ openAmount.toFixed(2) }}</div>
        <div class="stat-label">持仓金额</div>
      </div>
      <div class="stat-card" :class="openPnl >= 0 ? 'profit' : 'loss'">
        <div class="stat-num">{{ openPnl >= 0 ? '+' : '' }}{{ openPnl.toFixed(2) }}</div>
        <div class="stat-label">浮盈($)</div>
      </div>
    </div>

    <CrudTable ref="crudRef" api="admin/trading/position" perms="admin:trading"
      :columns="columns" :search-fields="searchFields" :show-keyword="false"
      :has-create="false" :has-edit="false" :has-delete="false" :action-width="0" />
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import CrudTable from '@/components/CrudTable/index.vue'
import type { CrudColumn, SearchField } from '@/components/CrudTable/types'

const crudRef = ref()
const openCount = ref(0)
const openAmount = ref(0)
const openPnl = ref(0)

async function loadOpenStats() {
  try {
    const data = await crudRef.value?.crud?.api?.getList?.({
      page: 1, pageSize: 100, filters: JSON.stringify({ status: 'open' }),
    })
    const list = data?.list || []
    openCount.value = data?.total ?? list.length
    openAmount.value = list.reduce((s: number, r: any) => s + Number(r.amount_usd || 0), 0)
    openPnl.value = list.reduce((s: number, r: any) => s + Number(r.pnl_usd || 0), 0)
  } catch {}
}

const columns: CrudColumn[] = [
  { field: 'id', label: 'ID', width: 70 },
  { field: 'entry_time', label: '入场时间', width: 165, type: 'time' },
  { field: 'token_symbol', label: 'Token', width: 110,
    formatter: (row: any) => row.token_symbol || '-' },
  { field: 'pool_address', label: '池', width: 110,
    formatter: (row: any) => row.pool_address ? `${row.pool_address.slice(0, 6)}…${row.pool_address.slice(-4)}` : '-' },
  { field: 'direction', label: '方向', width: 75, type: 'tag',
    tagMap: {
      long: { label: '多', type: 'success' },
      short: { label: '空', type: 'danger' },
    } },
  { field: 'amount_usd', label: '金额($)', width: 90, align: 'right',
    formatter: (row: any, v: any) => v != null ? Number(v).toFixed(2) : '-' },
  { field: 'entry_price', label: '入场价', width: 110, align: 'right',
    formatter: (row: any, v: any) => v != null ? Number(v).toPrecision(6) : '-' },
  { field: 'pnl_usd', label: '浮盈($)', width: 95, align: 'right',
    formatter: (row: any, v: any) => {
      if (v == null) return '-'
      const n = Number(v)
      return (n >= 0 ? '+' : '') + n.toFixed(2)
    } },
  { field: 'status', label: '状态', width: 85, align: 'center', type: 'status',
    statusMap: {
      open: { label: '持仓中', type: 'success' },
      closed: { label: '已平仓', type: 'info' },
    } },
  { field: 'run_id', label: '运行批次', minWidth: 190,
    formatter: (row: any) => row.run_id || '-' },
]

const searchFields: SearchField[] = [
  { field: 'status', label: '状态', type: 'select', options: [
    { label: '持仓中', value: 'open' },
    { label: '已平仓', value: 'closed' },
  ] },
  { field: 'token_symbol', label: 'Token', type: 'input', placeholder: 'token 符号' },
]

onMounted(() => {
  // 默认只看持仓中
  const crud = crudRef.value?.crud
  if (crud) {
    crud.queryParams.filters.status = 'open'
    crud.getList()
  }
  loadOpenStats()
})
</script>

<style scoped>
.stat-cards { display: flex; gap: 12px; margin-bottom: 16px; }
.stat-card {
  flex: 1; padding: 16px; border-radius: 8px; text-align: center;
  border: 1px solid var(--el-border-color-lighter); background: var(--el-bg-color);
}
.stat-num { font-size: 28px; font-weight: 700; line-height: 1.2; }
.stat-label { font-size: 13px; color: var(--el-text-color-secondary); margin-top: 4px; }
.stat-card.open .stat-num { color: var(--el-color-primary); }
.stat-card.amount .stat-num { color: var(--el-color-warning); }
.stat-card.profit .stat-num { color: var(--el-color-success); }
.stat-card.loss .stat-num { color: var(--el-color-danger); }
</style>
