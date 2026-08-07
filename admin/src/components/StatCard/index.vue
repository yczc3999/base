<template>
  <div
    class="stat-card"
    :class="{ 'is-clickable': isClickable }"
    :style="{ '--accent': accent }"
    :role="isClickable ? 'button' : undefined"
    :tabindex="isClickable ? 0 : undefined"
    :aria-label="isClickable ? label : undefined"
    @keydown.enter.prevent="onKeyboard"
    @keydown.space.prevent="onKeyboard"
  >
    <div v-if="icon" class="stat-icon">
      <component :is="icon" :size="22" :stroke-width="2.2" />
    </div>
    <div class="stat-info">
      <div class="stat-value">
        <CountUp v-if="count && typeof value === 'number'" :value="value" />
        <template v-else>{{ value }}</template>
      </div>
      <div class="stat-label">{{ label }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, useAttrs } from 'vue'
import type { LucideIcon } from 'lucide-vue-next'
import CountUp from '@/components/CountUp.vue'

defineOptions({ name: 'StatCard' })

withDefaults(defineProps<{
  /** 左侧色块图标（lucide-vue-next 组件） */
  icon?: LucideIcon
  value: string | number
  label: string
  /** 强调色（CSS 变量名），默认主题主色 */
  accent?: string
  /** 数值走 CountUp 数字滚动动画 */
  count?: boolean
}>(), {
  icon: undefined,
  accent: 'var(--primary)',
  count: false,
})

const attrs = useAttrs()
const isClickable = computed(() => Boolean(attrs.onClick))

/** 键盘可达：父级绑定 @click 后 Enter / Space 等价点击 */
function onKeyboard() {
  if (!isClickable.value) return
  ;(attrs.onClick as ((...args: unknown[]) => unknown) | undefined)?.()
}
</script>

<style scoped lang="scss">
.stat-card {
  display: flex;
  align-items: center;
  gap: var(--space-md);
  padding: var(--space-base) var(--space-lg);
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-left: 3px solid var(--accent);
  border-radius: 4px;
  transition: border-color var(--transition-fast);

  &.is-clickable {
    cursor: pointer;

    &:hover {
      border-color: var(--border-dark);
    }

    &:focus-visible {
      outline: 2px solid var(--primary);
      outline-offset: 1px;
    }
  }
}

.stat-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 40px;
  height: 40px;
  border-radius: 4px;
  background: color-mix(in srgb, var(--accent) 10%, transparent);
  color: var(--accent);
}

.stat-info {
  min-width: 0;
}

.stat-value {
  font-size: var(--text-2xl);
  font-weight: 700;
  line-height: 1;
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
}

.stat-label {
  margin-top: var(--space-xs);
  font-size: var(--text-xs);
  color: var(--text-secondary);
}
</style>
