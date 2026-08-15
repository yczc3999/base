<script setup lang="ts">
/**
 * 列表壳对齐 Base CrudTable：
 * - 筛选写在草稿里，点「搜索」才进查询
 * - 翻页用明确的上一页/下一页按钮（不依赖 el-pagination 的 prev-click）
 * - 分页条 sticky 钉在视口底部，表再长也能看见
 */
import { computed, reactive, watch } from 'vue'
import { Search, RefreshRight, ArrowLeft, ArrowRight } from '@element-plus/icons-vue'
import type { SearchField } from '@/components/CrudTable/types'
import PageState from './PageState.vue'

const props = withDefaults(defineProps<{
  rows: any[]
  loading: boolean
  error?: string | null
  denied?: boolean
  hasMore: boolean
  asOf?: string | null
  appliedFilters: Record<string, string>
  searchFields?: SearchField[]
  page: number
  pageSize: number
  canPrev: boolean
}>(), {
  error: null,
  denied: false,
  asOf: null,
  searchFields: () => [],
  appliedFilters: () => ({}),
})

const emit = defineEmits<{
  search: [filters: Record<string, string>]
  reset: []
  refresh: []
  next: []
  prev: []
  'size-change': [size: number]
  retry: []
}>()

const draft = reactive<Record<string, string>>({})

function hydrateDraft() {
  for (const key of Object.keys(draft)) delete draft[key]
  for (const field of props.searchFields) {
    draft[field.field] = props.appliedFilters[field.field] ?? ''
  }
}

watch(
  () => [props.searchFields, props.appliedFilters] as const,
  () => hydrateDraft(),
  { immediate: true, deep: true },
)

function onSearch() {
  emit('search', { ...draft })
}

function onReset() {
  for (const field of props.searchFields) draft[field.field] = ''
  emit('reset')
}

const pageSizeModel = computed({
  get: () => props.pageSize,
  set: (size: number) => emit('size-change', size),
})
</script>

<template>
  <div class="crud-view">
    <div v-if="searchFields.length" class="crud-search">
      <el-form :inline="true" @submit.prevent="onSearch">
        <el-form-item v-for="field in searchFields" :key="field.field" :label="field.label">
          <el-input
            v-if="field.type === 'input'"
            v-model="draft[field.field]"
            :placeholder="field.placeholder || `请输入${field.label}`"
            clearable
            style="width: 180px"
            @keyup.enter="onSearch"
          />
          <el-select
            v-else-if="field.type === 'select'"
            v-model="draft[field.field]"
            :placeholder="field.placeholder || `请选择${field.label}`"
            clearable
            style="width: 160px"
          >
            <el-option
              v-for="opt in field.options"
              :key="String(opt.value)"
              :label="opt.label"
              :value="opt.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :icon="Search" @click="onSearch">搜索</el-button>
          <el-button :icon="RefreshRight" @click="onReset">重置</el-button>
        </el-form-item>
      </el-form>
    </div>

    <div class="crud-toolbar">
      <div class="toolbar-left">
        <slot name="toolbar" />
      </div>
      <div class="toolbar-right">
        <span class="total-text">第 {{ page }} 页 · 本页 {{ rows.length }} 条</span>
        <el-button :icon="RefreshRight" title="刷新" @click="emit('refresh')" />
      </div>
    </div>

    <PageState
      :loading="loading"
      :error="error"
      :denied="denied"
      :empty="!loading && !error && !rows.length"
      @retry="emit('retry')"
    >
      <template #empty>
        <slot name="empty">
          <p class="t">暂无数据</p>
          <p class="m">库里还没有这类记录。</p>
        </slot>
      </template>
      <div class="crud-table">
        <el-skeleton v-if="loading && !rows.length" :rows="5" animated />
        <el-table v-else v-loading="loading" :data="rows" stripe style="width: 100%">
          <slot />
        </el-table>
      </div>
    </PageState>

    <div class="crud-pagination">
      <span class="total-text">第 {{ page }} 页 · 本页 {{ rows.length }} 条</span>
      <el-select v-model="pageSizeModel" class="size-select" :disabled="loading">
        <el-option :value="10" label="10 条/页" />
        <el-option :value="20" label="20 条/页" />
        <el-option :value="50" label="50 条/页" />
        <el-option :value="100" label="100 条/页" />
      </el-select>
      <el-button :icon="ArrowLeft" :disabled="!canPrev || loading" @click="emit('prev')">
        上一页
      </el-button>
      <el-button type="primary" :icon="ArrowRight" :disabled="!hasMore || loading" @click="emit('next')">
        下一页
      </el-button>
    </div>
  </div>
</template>

<style scoped lang="scss">
.crud-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-base);
  min-height: calc(100vh - 180px);
}

.crud-search {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: var(--space-base);
}

.crud-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0 var(--space-base);

  .toolbar-left { display: flex; gap: 8px; }
  .toolbar-right { display: flex; align-items: center; gap: 10px; }
}

.total-text { font-size: var(--text-xs); color: var(--text-secondary); }

.crud-table {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
}

.crud-pagination {
  position: sticky;
  bottom: 0;
  z-index: 4;
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 10px;
  margin-top: auto;
  padding: 12px var(--space-base);
  background: var(--bg-page);
  border-top: 1px solid var(--border);
}

.size-select { width: 120px; }
</style>
