<template>
  <CrudTable ref="crudRef" api="admin/role" perms="admin:role" :columns="columns" :search-fields="searchFields" :form-fields="formFields" />
</template>
<script setup lang="ts">
import CrudTable from '@/components/CrudTable/index.vue'
import type { CrudColumn, SearchField, FormField } from '@/components/CrudTable/types'
const crudRef = ref()
const columns: CrudColumn[] = [
  { field: 'id', label: 'ID', width: 70, sortable: 'custom' },
  { field: 'name', label: '角色标识', minWidth: 120 },
  { field: 'label', label: '显示名', minWidth: 120 },
  { field: 'remark', label: '备注', minWidth: 160 },
  { field: 'sort', label: '排序', width: 80, align: 'center' },
  { field: 'status', label: '状态', width: 90, align: 'center', type: 'status', statusMap: { 1: { label: '正常', type: 'success' }, 0: { label: '禁用', type: 'danger' } } },
  { field: 'created_at', label: '创建时间', width: 170, type: 'time', sortable: 'custom' },
]
const searchFields: SearchField[] = [
  { field: 'status', label: '状态', type: 'select', options: [{ label: '正常', value: 1 }, { label: '禁用', value: 0 }] },
]
const formFields: FormField[] = [
  { field: 'name', label: '角色标识', rules: [{ required: true, message: '请输入角色标识' }] },
  { field: 'label', label: '显示名', rules: [{ required: true, message: '请输入显示名' }] },
  { field: 'remark', label: '备注', type: 'textarea' },
  { field: 'sort', label: '排序', type: 'number', default: 0 },
  { field: 'status', label: '状态', type: 'switch', default: 1 },
]
</script>
