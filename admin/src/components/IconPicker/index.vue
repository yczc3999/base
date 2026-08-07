<template>
  <el-popover
    v-model:visible="visible"
    placement="bottom-start"
    :width="420"
    trigger="click"
  >
    <template #reference>
      <div class="icon-picker-trigger">
        <component :is="iconComponent" v-if="modelValue && iconComponent" :size="18" />
        <span v-else class="placeholder">选择图标</span>
        <span v-if="modelValue" class="icon-name">{{ modelValue }}</span>
        <span class="trigger-arrow">▾</span>
      </div>
    </template>

    <div class="icon-picker-panel">
      <!-- 搜索 -->
      <div class="picker-search">
        <el-input
          v-model="keyword"
          placeholder="搜索图标..."
          clearable
          size="small"
          :prefix-icon="SearchIcon"
        />
      </div>

      <!-- 分类 Tab -->
      <div class="picker-tabs">
        <button
          v-for="cat in categories"
          :key="cat"
          class="tab-item"
          :class="{ active: activeCategory === cat }"
          @click="activeCategory = cat"
        >
          {{ cat }}
        </button>
      </div>

      <!-- 图标网格 -->
      <div class="picker-grid">
        <div
          v-for="name in displayIcons"
          :key="name"
          class="icon-item"
          :class="{ selected: modelValue === name }"
          :title="name"
          @click="selectIcon(name)"
        >
          <component :is="getIcon(name)" :size="20" />
        </div>
        <div v-if="!displayIcons.length" class="empty">无匹配图标</div>
      </div>

      <!-- 清除 -->
      <div v-if="modelValue" class="picker-footer">
        <el-button size="small" text @click="selectIcon('')">清除选择</el-button>
        <span class="current">当前：{{ modelValue }}</span>
      </div>
    </div>
  </el-popover>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { Search as SearchIcon } from '@element-plus/icons-vue'
import * as icons from 'lucide-vue-next'
import { iconCategories, allIcons } from './icons'

const props = defineProps<{
  modelValue: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

const visible = ref(false)
const keyword = ref('')
const activeCategory = ref('常用')

const categories = computed(() => ['全部', ...Object.keys(iconCategories)])

// 当前选中图标的组件
const iconComponent = computed(() => {
  if (!props.modelValue) return null
  return (icons as any)[props.modelValue] || null
})

// 获取图标组件
function getIcon(name: string) {
  return (icons as any)[name] || null
}

// 当前分类 + 搜索过滤
const displayIcons = computed(() => {
  let list: string[]

  if (keyword.value) {
    // 搜索模式：从全部 lucide 图标中搜索
    const kw = keyword.value.toLowerCase()
    list = Object.keys(icons).filter(
      (name) => name !== 'default' && name !== 'createIcons' && name.toLowerCase().includes(kw)
    )
    return list.slice(0, 80) // 搜索最多显示 80 个
  }

  if (activeCategory.value === '全部') {
    list = allIcons
  } else {
    list = iconCategories[activeCategory.value] || []
  }

  return list
})

function selectIcon(name: string) {
  emit('update:modelValue', name)
  visible.value = false
}
</script>

<style scoped lang="scss">
.icon-picker-trigger {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 32px;
  padding: 0 10px;
  background: var(--bg-input);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  cursor: pointer;
  font-size: var(--text-sm);
  transition: border-color var(--transition-fast);
  min-width: 160px;

  &:hover { border-color: var(--border-dark); }

  .placeholder { color: var(--text-placeholder); }
  .icon-name { color: var(--text-secondary); flex: 1; }
  .trigger-arrow { color: var(--text-placeholder); font-size: 10px; margin-left: auto; }
}

.icon-picker-panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.picker-search {
  :deep(.el-input__wrapper) { height: 32px !important; }
}

.picker-tabs {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;

  .tab-item {
    padding: 3px 10px;
    font-size: 12px;
    background: var(--bg-page);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    cursor: pointer;
    color: var(--text-secondary);
    transition: all var(--transition-fast);

    &:hover { color: var(--primary); border-color: var(--primary); }
    &.active {
      background: var(--primary);
      color: white;
      border-color: var(--primary);
    }
  }
}

.picker-grid {
  display: grid;
  grid-template-columns: repeat(10, 1fr);
  gap: 4px;
  max-height: 240px;
  overflow-y: auto;

  .icon-item {
    width: 36px; height: 36px;
    display: flex;
    align-items: center;
    justify-content: center;
    border: 1px solid transparent;
    border-radius: var(--radius-sm);
    cursor: pointer;
    color: var(--text-secondary);
    transition: all var(--transition-fast);

    &:hover {
      background: var(--primary-bg);
      color: var(--primary);
      border-color: var(--primary);
    }

    &.selected {
      background: var(--primary);
      color: white;
      border-color: var(--primary);
    }
  }

  .empty {
    grid-column: 1 / -1;
    text-align: center;
    padding: 20px;
    color: var(--text-placeholder);
    font-size: var(--text-sm);
  }
}

.picker-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-top: 8px;
  border-top: 1px solid var(--border-light);

  .current { font-size: 12px; color: var(--text-placeholder); }
}
</style>
