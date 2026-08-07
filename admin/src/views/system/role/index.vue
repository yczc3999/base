<template>
  <SchemaCrudPage :schema="schema" />
</template>

<script setup lang="ts">
defineOptions({ name: 'Role' })
import SchemaCrudPage from '@/components/SchemaCrudPage/index.vue'
import type { CrudPageSchema } from '@/types/crudSchema'

// F5 · Schema 驱动示例页：一个 JSON 声明整页（PageShell + CrudTable + 可选统计卡）。
// 复杂业务页仍可保留自定义模板，schema 只覆盖标准 CRUD 场景。
const schema: CrudPageSchema = {
  title: '角色管理',
  subTitle: '系统角色与权限分配',
  api: 'admin/role',
  perms: 'admin:role',
  columns: [
    { field: 'id', label: 'ID', width: 70, sortable: 'custom' },
    { field: 'name', label: '角色标识', minWidth: 120 },
    { field: 'label', label: '显示名', minWidth: 120 },
    { field: 'remark', label: '备注', minWidth: 160 },
    { field: 'sort', label: '排序', width: 80, align: 'center' },
    {
      field: 'status', label: '状态', width: 90, align: 'center', type: 'status',
      statusMap: {
        1: { label: '正常', type: 'success' },
        0: { label: '禁用', type: 'danger' },
      },
    },
    { field: 'created_at', label: '创建时间', width: 170, type: 'time', sortable: 'custom' },
  ],
  filters: [
    {
      field: 'status', label: '状态', type: 'select',
      options: [
        { label: '正常', value: 1 },
        { label: '禁用', value: 0 },
      ],
    },
  ],
  formFields: [
    { field: 'name', label: '角色标识', rules: [{ required: true, message: '请输入角色标识' }] },
    { field: 'label', label: '显示名', rules: [{ required: true, message: '请输入显示名' }] },
    { field: 'remark', label: '备注', type: 'textarea' },
    { field: 'sort', label: '排序', type: 'number', default: 0 },
    { field: 'status', label: '状态', type: 'switch', default: 1 },
  ],
  exportable: false,
}
</script>
