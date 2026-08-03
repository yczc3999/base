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
        <el-button v-if="hasCreate && (perms ? hasPerms(`${perms}:create`) : true)" type="primary" :icon="PlusIcon" @click="crud.handleAdd">
          新增
        </el-button>
        <el-button
          v-if="hasDelete && (perms ? hasPerms(`${perms}:delete`) : true)"
          type="danger"
          :icon="DeleteIcon"
          :disabled="!crud.selections.value.length"
          @click="crud.handleBatchDelete"
        >
          删除
        </el-button>
        <el-button
          v-if="exportable && (perms ? hasPerms(`${perms}:export`) : true)"
          :icon="DownloadIcon"
          :loading="exportState.exporting.value"
          @click="handleExportClick"
        >
          导出
        </el-button>
        <slot name="toolbar" />
      </div>
      <div class="toolbar-right">
        <el-progress
          v-if="exportState.exporting.value"
          :percentage="exportState.progress.value"
          :stroke-width="16"
          :text-inside="true"
          style="width: 160px; margin-right: 8px;"
        />
        <span class="total-text">共 {{ crud.total.value }} 条</span>
        <el-button :icon="RefreshIcon" @click="crud.getList" title="刷新" />
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
            <!-- 列表 switch -->
            <template v-else-if="col.type === 'switch'">
              <el-switch
                :model-value="row[col.field]"
                :active-value="col.activeValue ?? 1"
                :inactive-value="col.inactiveValue ?? 0"
                :before-change="() => handleColumnSwitch(row, col.field, row[col.field] === (col.activeValue ?? 1) ? (col.inactiveValue ?? 0) : (col.activeValue ?? 1))"
              />
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
                v-if="hasEdit && (perms ? hasPerms(`${perms}:edit`) : true)"
                type="primary" link size="small" :icon="EditIcon"
                @click="crud.handleEdit(row)"
              >编辑</el-button>
              <el-button
                v-if="hasDelete && (perms ? hasPerms(`${perms}:delete`) : true)"
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
            <!-- 普通输入 -->
            <el-input
              v-if="!f.type || f.type === 'input'"
              v-model="crud.formData.value[f.field]"
              :placeholder="f.placeholder || `请输入${f.label}`"
              :disabled="f.disabled"
            />
            <!-- 密码 -->
            <el-input
              v-else-if="f.type === 'password'"
              v-model="crud.formData.value[f.field]"
              type="password"
              show-password
              :placeholder="f.placeholder || `请输入${f.label}`"
              :disabled="f.disabled"
            />
            <!-- 多行文本 -->
            <el-input
              v-else-if="f.type === 'textarea'"
              v-model="crud.formData.value[f.field]"
              type="textarea"
              :rows="3"
              :placeholder="f.placeholder"
              :disabled="f.disabled"
            />
            <!-- JSON -->
            <JsonEditor
              v-else-if="f.type === 'json'"
              v-model="crud.formData.value[f.field]"
              :placeholder="f.placeholder"
            />
            <!-- 数字（带后缀） -->
            <el-input
              v-else-if="f.type === 'number' && f.suffix"
              v-model.number="crud.formData.value[f.field]"
              :disabled="f.disabled"
              :placeholder="f.placeholder"
            >
              <template #append>{{ f.suffix }}</template>
            </el-input>
            <!-- 数字 -->
            <el-input-number
              v-else-if="f.type === 'number'"
              v-model="crud.formData.value[f.field]"
              :min="0"
              :disabled="f.disabled"
              :placeholder="f.placeholder"
              style="width: 100%"
            />
            <!-- 下拉选择 -->
            <el-select
              v-else-if="f.type === 'select'"
              v-model="crud.formData.value[f.field]"
              :placeholder="f.placeholder || `请选择${f.label}`"
              :disabled="f.disabled"
              style="width: 100%"
            >
              <el-option
                v-for="opt in f.options"
                :key="opt.value"
                :label="opt.label"
                :value="opt.value"
              />
            </el-select>
            <!-- 单选按钮组 -->
            <el-radio-group
              v-else-if="f.type === 'radio'"
              v-model="crud.formData.value[f.field]"
              :disabled="f.disabled"
            >
              <el-radio
                v-for="opt in f.options"
                :key="opt.value"
                :value="opt.value"
              >{{ opt.label }}</el-radio>
            </el-radio-group>
            <!-- 图片上传 -->
            <ImageUpload
              v-else-if="f.type === 'imageUpload'"
              v-model="crud.formData.value[f.field]"
              :category="f.placeholder || 'default'"
            />
            <!-- 开关 -->
            <el-switch
              v-else-if="f.type === 'switch'"
              v-model="crud.formData.value[f.field]"
              :active-value="f.options?.[0]?.value ?? 1"
              :inactive-value="f.options?.[1]?.value ?? 0"
              :disabled="f.disabled"
            />
            <!-- 日期选择 -->
            <el-date-picker
              v-else-if="f.type === 'date'"
              v-model="crud.formData.value[f.field]"
              type="date"
              value-format="YYYY-MM-DD"
              :placeholder="f.placeholder || `请选择${f.label}`"
              :disabled="f.disabled"
              style="width: 100%"
            />
            <!-- 日期时间选择 -->
            <el-date-picker
              v-else-if="f.type === 'dateTime'"
              v-model="crud.formData.value[f.field]"
              type="datetime"
              value-format="YYYY-MM-DD HH:mm:ss"
              :placeholder="f.placeholder || `请选择${f.label}`"
              :disabled="f.disabled"
              style="width: 100%"
            />
            <!-- 颜色选择 -->
            <el-color-picker
              v-else-if="f.type === 'color'"
              v-model="crud.formData.value[f.field]"
              :disabled="f.disabled"
            />
            <!-- 富文本（暂用 textarea 代替） -->
            <el-input
              v-else-if="f.type === 'editor'"
              v-model="crud.formData.value[f.field]"
              type="textarea"
              :rows="8"
              :placeholder="f.placeholder || 'Markdown 内容'"
              :disabled="f.disabled"
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
import JsonEditor from '@/components/JsonEditor/index.vue'
import { useExport } from '@/hooks/useExport'
import { Search as SearchIcon, RefreshRight as RefreshIcon, Plus as PlusIcon, Delete as DeleteIcon, Edit as EditIcon, Check as CheckIcon, Download as DownloadIcon } from '@element-plus/icons-vue'

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
  /** 是否支持导出 */
  exportable?: boolean
  /** 是否显示新增按钮 */
  hasCreate?: boolean
  /** 是否显示编辑按钮 */
  hasEdit?: boolean
  /** 是否显示删除按钮 */
  hasDelete?: boolean
}>(), {
  searchFields: () => [],
  formFields: () => [],
  perms: '',
  showKeyword: true,
  actionWidth: 180,
  dialogWidth: '560px',
  exportable: false,
  hasCreate: true,
  hasEdit: true,
  hasDelete: true,
})

const { hasPerms } = usePermission()
const formRef = ref()

const crud = useCrud({ api: props.api })
const exportState = useExport(crud.api)

function handleExportClick() {
  const filters = Object.keys(crud.queryParams.filters).length ? crud.queryParams.filters : undefined
  exportState.handleExport(filters)
}

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

// 列表内 switch 切换（before-change 模式，返回 Promise）
async function handleColumnSwitch(row: any, field: string, newVal: any): Promise<boolean> {
  try {
    await crud.api.doEdit({ id: row.id, [field]: newVal })
    row[field] = newVal
    return true
  } catch {
    return false
  }
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
  .toolbar-right { display: flex; align-items: center; gap: 10px; }
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
