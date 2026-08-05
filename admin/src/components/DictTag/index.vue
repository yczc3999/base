<template>
  <el-tag :type="tagType" size="small" v-bind="$attrs">{{ displayLabel }}</el-tag>
</template>

<script setup lang="ts">
/**
 * DictTag — 数据字典标签
 *
 * 按字典类型 + 值渲染中文标签：
 *   <DictTag type="gender" value="1" /> → 「男」
 *
 * 数据源: GET /api/dict/items?type={type}（无 auth 公开端点）
 * 缓存由 src/api/dict.ts 的模块级 Map 持有，跨组件共享、同 type 只请求一次。
 *
 * props:
 *   type     字典类型名（如 gender）
 *   value    字典项值（如 1 / "male"）
 *   fallback 找不到匹配项时显示的文字（默认显示原值）
 *   tagType  标签样式（默认 info）
 */
import { ref, watch, computed, onMounted } from 'vue'
import { getDictItems } from '@/api/dict'

const props = defineProps<{
  type: string
  value: any
  fallback?: string
  tagType?: 'primary' | 'success' | 'warning' | 'info' | 'danger'
}>()

const label = ref('')

const displayLabel = computed(() => label.value || props.fallback || String(props.value ?? '—'))
const tagType = computed(() => props.tagType || 'info')

async function load() {
  if (!props.type) {
    label.value = ''
    return
  }
  const items = await getDictItems(props.type)
  const match = items.find((i) => String(i.value) === String(props.value))
  label.value = match?.label ?? ''
}

onMounted(load)
watch(() => [props.type, props.value] as const, load, { deep: true })
</script>
