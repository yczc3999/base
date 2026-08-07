<template>
  <span>{{ displayText }}</span>
</template>

<script setup lang="ts">
import { ref, watch, onBeforeUnmount } from 'vue'

const props = withDefaults(defineProps<{
  value: number
  /** 动画时长 ms */
  duration?: number
  /** 小数位 */
  decimals?: number
}>(), {
  duration: 700,
  decimals: 0,
})

const display = ref(props.value)
const displayText = computed(() => display.value.toFixed(props.decimals))

let raf = 0
watch(
  () => props.value,
  (newVal) => {
    cancelAnimationFrame(raf)
    const from = display.value
    const start = performance.now()
    const step = (now: number) => {
      const t = Math.min(1, (now - start) / props.duration)
      const eased = 1 - Math.pow(1 - t, 3) // ease-out-cubic
      display.value = from + (newVal - from) * eased
      if (t < 1) raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
  },
)

onBeforeUnmount(() => cancelAnimationFrame(raf))
</script>
