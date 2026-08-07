<template>
  <CrudTable api="admin/message" perms="admin:message" :columns="columns" :search-fields="searchFields" :action-width="100">
    <template #actions="{ row }">
      <el-button v-if="!row.is_read" type="primary" link size="small" @click="markRead(row)">已读</el-button>
      <el-button type="danger" link size="small" @click="crudRef?.crud.handleDelete(row)">删除</el-button>
    </template>
  </CrudTable>
</template>
<script setup lang="ts">
defineOptions({ name: 'Message' })
import CrudTable from '@/components/CrudTable/index.vue'
import type { CrudColumn, SearchField } from '@/components/CrudTable/types'
import messageApi from '@/api/modules/message'
import { ElMessage } from 'element-plus/es/components/message/index'
const crudRef = ref()
const columns: CrudColumn[] = [
  { field: 'id', label: 'ID', width: 70 },
  { field: 'title', label: '标题', minWidth: 200 },
  { field: 'type', label: '类型', width: 90, type: 'tag', tagMap: { 0: { label: '系统', type: 'info' }, 1: { label: '审批', type: 'warning' }, 2: { label: '告警', type: 'danger' } } },
  { field: 'is_read', label: '状态', width: 80, type: 'status', statusMap: { true: { label: '已读', type: 'info' }, false: { label: '未读', type: 'success' } } },
  { field: 'created_at', label: '时间', width: 170, type: 'time', sortable: 'custom' },
]
const searchFields: SearchField[] = [
  { field: 'is_read', label: '状态', type: 'select', options: [{ label: '未读', value: false }, { label: '已读', value: true }] },
]
async function markRead(row: any) {
  await messageApi.markRead(row.id)
  ElMessage.success('已标记已读')
  crudRef.value?.crud.getList()
}
</script>
