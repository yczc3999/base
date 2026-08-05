<template>
  <div class="dict-page">
    <!-- 字典类型列表 -->
    <CrudTable ref="dictTableRef" api="admin/dict" perms="admin:dict"
      :columns="dictColumns" :search-fields="dictSearchFields" :form-fields="dictFormFields"
      :action-width="200">
      <template #actions="{ row }">
        <el-button type="primary" link size="small" :icon="Collection" @click="openItems(row)">
          字典项
        </el-button>
        <el-button type="primary" link size="small" :icon="EditIcon"
          @click="dictTableRef?.crud.handleEdit(row)">编辑</el-button>
        <el-button type="danger" link size="small" :icon="DeleteIcon"
          @click="dictTableRef?.crud.handleDelete(row)">删除</el-button>
      </template>
    </CrudTable>

    <!-- 字典项管理弹窗 -->
    <el-dialog v-model="itemDialogVisible" :title="`字典项 — ${currentDict?.type_name || ''}`"
      width="860px" destroy-on-close @opened="onItemsDialogOpened" @closed="onItemsDialogClosed">
      <CrudTable v-if="currentDict" ref="itemTableRef" api="admin/dict_item" perms="admin:dict"
        :columns="itemColumns" :search-fields="itemSearchFields" :form-fields="itemFormFields"
        :action-width="160" :show-keyword="true">
        <template #toolbar>
          <span class="item-tip">管理「{{ currentDict.type_name }}」下的字典项，删除字典时自动清理全部项</span>
        </template>
      </CrudTable>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, nextTick } from 'vue'
import { Collection, Edit as EditIcon, Delete as DeleteIcon } from '@element-plus/icons-vue'
import CrudTable from '@/components/CrudTable/index.vue'
import type { CrudColumn, SearchField, FormField } from '@/components/CrudTable/types'
import { clearDictCache } from '@/api/dict'

const dictTableRef = ref()
const itemTableRef = ref()

// ---- 字典类型表 ----

const dictColumns: CrudColumn[] = [
  { field: 'id', label: 'ID', width: 60 },
  { field: 'type_name', label: '类型名', minWidth: 120 },
  { field: 'description', label: '描述', minWidth: 180 },
  { field: 'status', label: '状态', width: 80, align: 'center', type: 'switch' },
  { field: 'created_at', label: '创建时间', width: 160, type: 'time' },
]

const dictSearchFields: SearchField[] = [
  { field: 'keyword', label: '搜索', type: 'input', placeholder: '类型名 / 描述' },
  { field: 'status', label: '状态', type: 'select', options: [
    { label: '启用', value: 1 }, { label: '禁用', value: 0 },
  ] },
]

const dictFormFields: FormField[] = [
  { field: 'type_name', label: '类型名', rules: [{ required: true, message: '请输入类型名' }],
    placeholder: '如 gender / status / article_type' },
  { field: 'description', label: '描述', type: 'textarea' },
  { field: 'status', label: '状态', type: 'switch', default: 1, options: [{ value: 1, label: '启用' }] },
]

// ---- 字典项弹窗 ----

const itemDialogVisible = ref(false)
const currentDict = ref<any>(null)
const itemCrud = computed(() => itemTableRef.value?.crud)

function openItems(row: any) {
  currentDict.value = row
  itemDialogVisible.value = true
}

async function onItemsDialogOpened() {
  await nextTick()
  // 只展示当前字典的项
  itemTableRef.value?.crud.setQuery({ filters: { dict_id: currentDict.value.id } })
}

function onItemsDialogClosed() {
  currentDict.value = null
  // 项可能有增删改，清 DictTag 缓存
  clearDictCache()
}

// 字典类型表保存后清 DictTag 缓存（类型名被改 / 状态被禁用时，Tag 要即时反映）
watch(
  () => dictTableRef.value?.crud?.formVisible?.value,
  (visible, prev) => {
    if (prev === true && visible === false) clearDictCache()
  },
)

// 新增项时自动注入 dict_id（表单里不显示该字段）
watch(
  () => [itemCrud.value?.formVisible?.value, itemCrud.value?.formMode?.value] as const,
  ([visible, mode]) => {
    if (visible && mode === 'create' && currentDict.value) {
      itemCrud.value.formData.value = { dict_id: currentDict.value.id }
    }
  },
)

const itemColumns: CrudColumn[] = [
  { field: 'id', label: 'ID', width: 60 },
  { field: 'value', label: '值', minWidth: 100 },
  { field: 'label', label: '标签', minWidth: 120 },
  { field: 'sort', label: '排序', width: 70 },
  { field: 'status', label: '状态', width: 80, align: 'center', type: 'switch' },
  { field: 'created_at', label: '创建时间', width: 160, type: 'time' },
]

const itemSearchFields: SearchField[] = [
  { field: 'keyword', label: '搜索', type: 'input', placeholder: '值 / 标签' },
  { field: 'status', label: '状态', type: 'select', options: [
    { label: '启用', value: 1 }, { label: '禁用', value: 0 },
  ] },
]

const itemFormFields: FormField[] = [
  { field: 'value', label: '值', rules: [{ required: true, message: '请输入值' }],
    placeholder: '存储值，如 1 / male / draft' },
  { field: 'label', label: '标签', rules: [{ required: true, message: '请输入标签' }],
    placeholder: '显示文字，如 男 / 草稿' },
  { field: 'sort', label: '排序', type: 'number', default: 0 },
  { field: 'status', label: '状态', type: 'switch', default: 1, options: [{ value: 1, label: '启用' }] },
]
</script>

<style scoped>
.item-tip {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
