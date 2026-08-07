<template>
  <CrudTable
    ref="crudRef"
    api="admin/user"
    perms="admin:user"
    :columns="columns"
    :search-fields="searchFields"
    :form-fields="formFields"
  />
</template>

<script setup lang="ts">
defineOptions({ name: 'AdminUser' })
import CrudTable from '@/components/CrudTable/index.vue'
import type { CrudColumn, SearchField, FormField } from '@/components/CrudTable/types'

const columns: CrudColumn[] = [
  { field: 'id', label: 'ID', width: 70, sortable: 'custom' },
  { field: 'username', label: '用户名', minWidth: 120 },
  { field: 'nickname', label: '昵称', minWidth: 100 },
  { field: 'email', label: '邮箱', minWidth: 160 },
  { field: 'phone', label: '手机号', minWidth: 120 },
  {
    field: 'status', label: '状态', width: 90, align: 'center',
    type: 'status',
    statusMap: {
      1: { label: '正常', type: 'success' },
      0: { label: '禁用', type: 'danger' },
    },
  },
  { field: 'created_at', label: '创建时间', width: 170, type: 'time', sortable: 'custom' },
]

const searchFields: SearchField[] = [
  {
    field: 'status', label: '状态', type: 'select',
    options: [
      { label: '正常', value: 1 },
      { label: '禁用', value: 0 },
    ],
  },
]

const formFields: FormField[] = [
  { field: 'username', label: '用户名', rules: [{ required: true, message: '请输入用户名' }] },
  { field: 'password', label: '密码', type: 'password', rules: [{ required: true, message: '请输入密码' }], showOnCreate: true },
  { field: 'nickname', label: '昵称' },
  { field: 'avatar', label: '头像', type: 'imageUpload', placeholder: 'avatar' },
  { field: 'email', label: '邮箱' },
  { field: 'phone', label: '手机号' },
  { field: 'status', label: '状态', type: 'switch', default: 1 },
]
</script>
