<template>
  <CrudTable api="admin/loginLog" perms="admin:log:login" :columns="columns" :show-keyword="true" :action-width="0" exportable />
</template>
<script setup lang="ts">
import CrudTable from '@/components/CrudTable/index.vue'
import type { CrudColumn } from '@/components/CrudTable/types'
const columns: CrudColumn[] = [
  { field: 'id', label: 'ID', width: 70 },
  { field: 'username', label: '用户', width: 120 },
  { field: 'ip', label: 'IP', width: 130 },
  { field: 'status', label: '状态', width: 90, align: 'center', type: 'status', statusMap: { 1: { label: '成功', type: 'success' }, 0: { label: '失败', type: 'danger' } } },
  { field: 'remark', label: '备注', minWidth: 200 },
  { field: 'created_at', label: '时间', width: 170, type: 'time', sortable: 'custom' },
]
</script>
