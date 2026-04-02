<template>
  <CrudTable api="admin/operationLog" perms="admin:log:operation" :columns="columns" :show-keyword="true" :action-width="0" />
</template>
<script setup lang="ts">
import CrudTable from '@/components/CrudTable/index.vue'
import type { CrudColumn } from '@/components/CrudTable/types'
const columns: CrudColumn[] = [
  { field: 'id', label: 'ID', width: 70 },
  { field: 'username', label: '用户', width: 100 },
  { field: 'module', label: '模块', width: 100 },
  { field: 'action', label: '操作', width: 100 },
  { field: 'method', label: '方法', width: 70, align: 'center' },
  { field: 'url', label: 'URL', minWidth: 200 },
  { field: 'ip', label: 'IP', width: 130 },
  { field: 'status_code', label: '状态码', width: 80, align: 'center' },
  { field: 'duration', label: '耗时(ms)', width: 90, align: 'right' },
  { field: 'created_at', label: '时间', width: 170, type: 'time', sortable: 'custom' },
]
</script>
