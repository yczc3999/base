<script setup lang="ts">
/** WP-07B 状态徽标：文本 + 色块双编码，仅表达展示语义（不重算业务状态）。 */
const props = defineProps<{ tone?: 'success'|'warning'|'danger'|'info'|'neutral' }>()
const cls = computed(() => props.tone ?? 'neutral')
import { computed } from 'vue'
</script>
<template>
  <span class="v2-badge" :class="`tone-${cls}`"><span class="dot"></span><span class="txt"><slot /></span></span>
</template>
<style scoped>
.v2-badge{display:inline-flex;align-items:center;gap:6px;height:24px;padding:0 10px;border-radius:var(--v2-radius-full);font-size:12px;font-weight:600;white-space:nowrap}
.dot{width:8px;height:8px;border-radius:var(--v2-radius-full);flex:none}
.tone-success{background:var(--v2-success-soft);color:var(--v2-success)} .tone-success .dot{background:var(--v2-success)}
.tone-warning{background:var(--v2-warning-soft);color:var(--v2-warning)} .tone-warning .dot{background:var(--v2-warning)}
.tone-danger{background:var(--v2-danger-soft);color:var(--v2-danger)} .tone-danger .dot{background:var(--v2-danger)}
.tone-info{background:var(--v2-info-soft);color:var(--v2-info)} .tone-info .dot{background:var(--v2-info)}
.tone-neutral{background:#EEE9DC;color:var(--v2-ink-muted)} .tone-neutral .dot{background:var(--v2-ink-muted)}
</style>
