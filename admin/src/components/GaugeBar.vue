<template>
  <div class="gauge" role="progressbar" :aria-valuenow="Math.round(percent)" aria-valuemin="0" aria-valuemax="100">
    <div class="gauge-track">
      <div class="gauge-fill" :class="levelClass" :style="{ width: `${clamped}%` }" />
    </div>
    <span class="gauge-pct" :class="levelClass">{{ Math.round(clamped) }}%</span>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(defineProps<{
  /** 0-100 */
  percent: number
  /** 分档阈值：> dangerHigh → 红，> warnHigh → 黄，否则绿 */
  dangerHigh?: number
  warnHigh?: number
}>(), {
  dangerHigh: 90,
  warnHigh: 70,
})

const clamped = computed(() => Math.max(0, Math.min(100, props.percent)))

const levelClass = computed(() => {
  const p = clamped.value
  if (p > props.dangerHigh) return 'is-danger'
  if (p > props.warnHigh) return 'is-warning'
  return 'is-success'
})
</script>

<style scoped lang="scss">
.gauge {
  display: flex;
  align-items: center;
  gap: 12px;
}

.gauge-track {
  flex: 1;
  height: 8px;
  background: var(--border-light);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  overflow: hidden;
}

.gauge-fill {
  height: 100%;
  border-radius: var(--radius-sm);
  transition: width 800ms var(--transition-base);
  min-width: 0;

  &.is-success { background: var(--success); }
  &.is-warning { background: var(--warning); }
  &.is-danger  { background: var(--danger); }
}

.gauge-pct {
  min-width: 44px;
  text-align: right;
  font-size: var(--text-xl);
  font-weight: 700;
  font-variant-numeric: tabular-nums;

  &.is-success { color: var(--success); }
  &.is-warning { color: var(--warning); }
  &.is-danger  { color: var(--danger); }
}
</style>
