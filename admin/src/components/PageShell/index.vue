<template>
  <div class="page-shell">
    <header class="ps-head">
      <div class="ps-titles">
        <h2 class="ps-title">{{ title }}</h2>
        <p v-if="subTitle" class="ps-subtitle">{{ subTitle }}</p>
      </div>
      <div class="ps-actions">
        <slot name="actions" />
      </div>
    </header>
    <main class="ps-body">
      <!-- loading 态：骨架屏（loaded 之后即使 loading 也不再遮蔽，避免刷新闪烁） -->
      <el-skeleton v-if="loading && !loaded" :rows="rows" animated />
      <slot v-else />
    </main>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'

defineOptions({ name: 'PageShell' })

const props = withDefaults(defineProps<{
  /** 页面标题 */
  title: string
  /** 副标题（可选） */
  subTitle?: string
  /** loading 态：显示骨架屏 */
  loading?: boolean
  /** 骨架屏行数 */
  rows?: number
}>(), {
  subTitle: '',
  loading: false,
  rows: 4,
})

// 一旦内容真正渲染过（loaded），后续 loading 只做增量更新不再整体遮蔽
const loaded = ref(false)
watch(
  () => props.loading,
  (v) => { if (!v && !loaded.value) loaded.value = true },
)
</script>

<style scoped lang="scss">
.page-shell {
  display: flex;
  flex-direction: column;
  gap: var(--space-base);
}

.ps-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-base);
  padding-bottom: var(--space-base);
  border-bottom: 1px solid var(--border);
}

.ps-titles { display: flex; flex-direction: column; gap: var(--space-xs); }

.ps-title {
  font-size: var(--text-xl);
  font-weight: 600;
  color: var(--text-primary);
  line-height: 1.2;
  margin: 0;
}

.ps-subtitle {
  font-size: var(--text-xs);
  color: var(--text-secondary);
  margin: 0;
}

.ps-actions {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  flex-shrink: 0;
}

.ps-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-base);
}
</style>
