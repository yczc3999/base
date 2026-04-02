<template>
  <div class="crud-view">
    <!-- 搜索栏 -->
    <div v-if="searchFields.length" class="crud-search">
      <el-form :inline="true" @submit.prevent="crud.handleSearch">
        <!-- keyword -->
        <el-form-item v-if="showKeyword">
          <el-input
            v-model="crud.queryParams.keyword"
            placeholder="搜索关键词"
            clearable
            style="width: 200px"
            @keyup.enter="crud.handleSearch"
          />
        </el-form-item>
        <!-- 自定义搜索字段 -->
        <el-form-item v-for="f in searchFields" :key="f.field" :label="f.label">
          <el-input
            v-if="f.type === 'input'"
            v-model="crud.queryParams.filters[f.field]"
            :placeholder="f.placeholder || `请输入${f.label}`"
            clearable
            style="width: 180px"
          />
          <el-select
            v-else-if="f.type === 'select'"
            v-model="crud.queryParams.filters[f.field]"
            :placeholder="f.placeholder || `请选择${f.label}`"
            clearable
            style="width: 150px"
          >
            <el-option
              v-for="opt in f.options"
              :key="opt.value"
              :label="opt.label"
              :value="opt.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :icon="SearchIcon" @click="crud.handleSearch">搜索</el-button>
          <el-button :icon="RefreshIcon" @click="crud.handleReset">重置</el-button>
        </el-form-item>
      </el-form>
    </div>

    <!-- 工具栏 -->
    <div class="crud-toolbar">
      <div class="toolbar-left">
        <el-button v-if="perms ? hasPerms(`${perms}:create`) : true" type="primary" :icon="PlusIcon" @click="crud.handleAdd">
          新增
        </el-button>
        <el-button
          v-if="perms ? hasPerms(`${perms}:delete`) : true"
          type="danger"
          plain
          :icon="DeleteIcon"
          :disabled="!crud.selections.value.length"
          @click="crud.handleBatchDelete"
        >
          删除
        </el-button>
        <slot name="toolbar" />
      </div>
      <div class="toolbar-right">
        <span class="total-text">共 {{ crud.total.value }} 条</span>
      </div>
    </div>

    <!-- 表格 -->
    <div class="crud-table">
      <el-table
        v-loading="crud.loading.value"
        :data="crud.tableData.value"
        border
        @selection-change="crud.handleSelectionChange"
        @sort-change="crud.handleSortChange"
        style="width: 100%"
      >
        <el-table-column type="selection" width="40" fixed="left" />
        <el-table-column
          v-for="col in columns"
          :key="col.field"
          :prop="col.field"
          :label="col.label"
          :width="col.width"
          :min-width="col.minWidth || 120"
          :align="col.align || 'left'"
          :sortable="col.sortable"
          :fixed="col.fixed"
        >
          <template #default="{ row }">
            <!-- 状态 -->
            <template v-if="col.type === 'status' && col.statusMap">
              <span
                class="status-badge"
                :class="col.statusMap[row[col.field]]?.type || 'info'"
              >
                {{ col.statusMap[row[col.field]]?.label || row[col.field] }}
              </span>
            </template>
            <!-- tag -->
            <template v-else-if="col.type === 'tag' && col.tagMap">
              <el-tag :type="col.tagMap[row[col.field]]?.type || 'info'" size="small">
                {{ col.tagMap[row[col.field]]?.label || row[col.field] }}
              </el-tag>
            </template>
            <!-- 时间 -->
            <template v-else-if="col.type === 'time'">
              {{ formatTime(row[col.field]) }}
            </template>
            <!-- 自定义 formatter -->
            <template v-else-if="col.formatter">
              {{ col.formatter(row, row[col.field]) }}
            </template>
            <!-- 默认 -->
            <template v-else>{{ row[col.field] ?? '—' }}</template>
          </template>
        </el-table-column>

        <!-- 操作列 -->
        <el-table-column label="操作" :width="actionWidth" fixed="right" align="center">
          <template #default="{ row }">
            <slot name="actions" :row="row">
              <el-button
                v-if="perms ? hasPerms(`${perms}:edit`) : true"
                type="primary" link size="small" :icon="EditIcon"
                @click="crud.handleEdit(row)"
              >编辑</el-button>
              <el-button
                v-if="perms ? hasPerms(`${perms}:delete`) : true"
                type="danger" link size="small" :icon="DeleteIcon"
                @click="crud.handleDelete(row)"
              >删除</el-button>
            </slot>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- 分页 -->
    <div class="crud-pagination">
      <el-pagination
        v-model:current-page="crud.queryParams.page"
        v-model:page-size="crud.queryParams.pageSize"
        :total="crud.total.value"
        :page-sizes="[10, 20, 50, 100]"
        layout="total, sizes, prev, pager, next"
        @current-change="crud.handlePageChange"
        @size-change="crud.handleSizeChange"
      />
    </div>

    <!-- 编辑弹窗 -->
    <el-dialog
      v-model="crud.formVisible.value"
      :title="crud.formMode.value === 'create' ? '新增' : '编辑'"
      :width="dialogWidth"
      destroy-on-close
    >
      <el-form
        ref="formRef"
        :model="crud.formData.value"
        :rules="formRules"
        label-width="100px"
      >
        <template v-for="f in visibleFormFields" :key="f.field">
          <el-form-item :label="f.label" :prop="f.field" :rules="f.rules">
            <el-input
              v-if="!f.type || f.type === 'input'"
              v-model="crud.formData.value[f.field]"
              :placeholder="f.placeholder || `请输入${f.label}`"
            />
            <el-input
              v-else-if="f.type === 'password'"
              v-model="crud.formData.value[f.field]"
              type="password"
              show-password
              :placeholder="f.placeholder || `请输入${f.label}`"
            />
            <el-input
              v-else-if="f.type === 'textarea'"
              v-model="crud.formData.value[f.field]"
              type="textarea"
              :rows="3"
            />
            <el-input-number
              v-else-if="f.type === 'number'"
              v-model="crud.formData.value[f.field]"
              :min="0"
            />
            <el-select
              v-else-if="f.type === 'select'"
              v-model="crud.formData.value[f.field]"
              :placeholder="f.placeholder || `请选择${f.label}`"
              style="width: 100%"
            >
              <el-option
                v-for="opt in f.options"
                :key="opt.value"
                :label="opt.label"
                :value="opt.value"
              />
            </el-select>
            <ImageUpload
              v-else-if="f.type === 'imageUpload'"
              v-model="crud.formData.value[f.field]"
              :category="f.placeholder || 'default'"
            />
            <el-switch
              v-else-if="f.type === 'switch'"
              v-model="crud.formData.value[f.field]"
              :active-value="1"
              :inactive-value="0"
            />
          </el-form-item>
        </template>
        <slot name="form" :data="crud.formData.value" :mode="crud.formMode.value" />
      </el-form>
      <template #footer>
        <div class="dialog-footer">
          <el-button @click="crud.formVisible.value = false">取消</el-button>
          <el-button type="primary" :icon="CheckIcon" :loading="crud.formLoading.value" @click="submitForm">确 定</el-button>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useCrud } from '@/hooks/useCrud'
import { usePermission } from '@/hooks/usePermission'
import type { CrudColumn, SearchField, FormField } from './types'
import type { CrudApi } from '@/api/crud'
import ImageUpload from '@/components/ImageUpload/index.vue'
import { Search as SearchIcon, RefreshRight as RefreshIcon, Plus as PlusIcon, Delete as DeleteIcon, Edit as EditIcon, Check as CheckIcon } from '@element-plus/icons-vue'

const props = withDefaults(defineProps<{
  /** API 路径（如 'admin/user'）或 CrudApi 对象 */
  api: string | CrudApi
  /** 表格列 */
  columns: CrudColumn[]
  /** 搜索字段 */
  searchFields?: SearchField[]
  /** 表单字段 */
  formFields?: FormField[]
  /** 权限前缀（如 'admin:user'） */
  perms?: string
  /** 是否显示 keyword 搜索框 */
  showKeyword?: boolean
  /** 操作列宽度 */
  actionWidth?: number
  /** 弹窗宽度 */
  dialogWidth?: string | number
}>(), {
  searchFields: () => [],
  formFields: () => [],
  perms: '',
  showKeyword: true,
  actionWidth: 180,
  dialogWidth: '560px',
})

const { hasPerms } = usePermission()
const formRef = ref()

const crud = useCrud({ api: props.api })

// 根据模式过滤表单字段
const visibleFormFields = computed(() => {
  return (props.formFields || []).filter((f) => {
    if (crud.formMode.value === 'create' && f.showOnEdit) return false
    if (crud.formMode.value === 'edit' && f.showOnCreate) return false
    return true
  })
})

// 校验规则
const formRules = computed(() => {
  const rules: Record<string, any> = {}
  for (const f of visibleFormFields.value) {
    if (f.rules?.length) rules[f.field] = f.rules
  }
  return rules
})

// 提交
async function submitForm() {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return
  crud.handleSubmit()
}

// 时间格式化
function formatTime(val: string) {
  if (!val) return '—'
  return val.replace('T', ' ').substring(0, 19)
}

// 暴露 crud 给父组件
defineExpose({ crud })
</script>

<style scoped lang="scss">
.crud-view {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.crud-search {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 16px 16px 0;
}

.crud-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;

  .toolbar-left { display: flex; gap: 8px; }
  .total-text { font-size: var(--text-xs); color: var(--text-secondary); }
}

.crud-table {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
}

.crud-pagination {
  display: flex;
  justify-content: flex-end;
  padding: 8px 0;
}

// 状态 badge
.status-badge {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  font-size: var(--text-xs);
  font-weight: 500;
  border-radius: var(--radius-sm);

  &.success { background: var(--success-bg); color: var(--success); }
  &.danger  { background: var(--danger-bg); color: var(--danger); }
  &.warning { background: var(--warning-bg); color: var(--warning); }
  &.info    { background: var(--info-bg); color: var(--info); }
}
</style>
