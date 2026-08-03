<template>
  <div>
    <CrudTable ref="crudRef" api="admin/trading/trade" perms="admin:trading"
      :columns="columns" :search-fields="searchFields"
      :has-create="false" :has-edit="false" :has-delete="false"
      exportable :action-width="0" />
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import CrudTable from '@/components/CrudTable/index.vue'
import type { CrudColumn, SearchField } from '@/components/CrudTable/types'

const crudRef = ref()

function fmtPnl(row: any, value: any) {
  if (value === null || value === undefined) return '-'
  const n = Number(value)
  return (n >= 0 ? '+' : '') + n.toFixed(2)
}

function fmtPrice(row: any, value: any) {
  if (value === null || value === undefined) return '-'
  return Number(value).toPrecision(6)
}

const columns: CrudColumn[] = [
  { field: 'id', label: 'ID', width: 70 },
  { field: 'entry_time', label: '入场时间', width: 165, type: 'time' },
  { field: 'exit_time', label: '出场时间', width: 165, type: 'time' },
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
  { field: 'entry_price', label: '入场价', width: 110, align: 'right', formatter: fmtPrice },
  { field: 'exit_price', label: '出场价', width: 110, align: 'right', formatter: fmtPrice },
  { field: 'pnl_usd', label: 'PnL($)', width: 95, align: 'right', formatter: fmtPnl },
  { field: 'exit_reason', label: '退出原因', width: 100,
    formatter: (row: any) => row.exit_reason || '-' },
  { field: 'source', label: '来源', width: 85, type: 'tag',
    tagMap: {
      PAPER: { label: 'PAPER', type: 'info' },
      SHADOW: { label: 'SHADOW', type: 'warning' },
      LIVE: { label: 'LIVE', type: 'danger' },
    } },
  { field: 'run_id', label: '运行批次', minWidth: 190,
    formatter: (row: any) => row.run_id || '-' },
]

const searchFields: SearchField[] = [
  { field: 'token_symbol', label: 'Token', type: 'input', placeholder: 'token 符号' },
  { field: 'source', label: '来源', type: 'select', options: [
    { label: 'PAPER', value: 'PAPER' },
    { label: 'SHADOW', value: 'SHADOW' },
    { label: 'LIVE', value: 'LIVE' },
  ] },
  { field: 'exit_reason', label: '退出原因', type: 'input', placeholder: '如 tp / sl / timeout' },
]
</script>
