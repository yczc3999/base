<template>
  <div class="empty-state" :style="{ padding: padding }">
    <div class="empty-icon" :class="`is-${tone}`">
      <component :is="icon" :size="32" :stroke-width="1.6" />
    </div>
    <p class="empty-title">{{ title }}</p>
    <p v-if="description" class="empty-desc">{{ description }}</p>
    <div v-if="$slots.action" class="empty-action">
      <slot name="action" />
    </div>
  </div>
</template>

<script setup lang="ts">
import type { LucideIcon } from 'lucide-vue-next'
import { Inbox } from 'lucide-vue-next'

defineOptions({ name: 'EmptyState' })

withDefaults(defineProps<{
  /** 大色块图标（lucide 组件） */
  icon?: LucideIcon
  title?: string
  description?: string
  /** 色块基调 */
  tone?: 'primary' | 'muted'
  /** 内边距 */
  padding?: string
}>(), {
  icon: Inbox,
  title: '暂无数据',
  description: '',
  tone: 'muted',
  padding: '48px 24px',
})
</script>

<style scoped lang="scss">
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  gap: var(--space-xs);
}

.empty-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 64px;
  height: 64px;
  border-radius: var(--radius);
  margin-bottom: var(--space-sm);

  &.is-primary { background: var(--primary-bg); color: var(--primary); }
  &.is-muted   { background: var(--bg-input); color: var(--text-disabled); }
}

.empty-title {
  font-size: var(--text-base);
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
}

.empty-desc {
  font-size: var(--text-xs);
  color: var(--text-secondary);
  margin: 0;
}

.empty-action {
  margin-top: var(--space-base);
}
</style>
