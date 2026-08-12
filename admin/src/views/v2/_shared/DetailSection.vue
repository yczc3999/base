<script setup lang="ts">
/** WP-07B 详情分区：标题 + 可选 lead + 槽。 */
defineProps<{ title: string; lead?: string }>()
</script>
<template>
  <section class="v2-section">
    <h2>{{ title }}</h2>
    <p v-if="lead" class="lead">{{ lead }}</p>
    <slot />
  </section>
</template>
<style scoped>
.v2-section{background:var(--v2-surface);border:var(--v2-border-w) solid var(--v2-line);border-radius:var(--v2-radius-md);margin-bottom:var(--v2-space-6);padding:var(--v2-space-4);min-width:0;overflow-x:auto}
.v2-section h2{font-size:15px;font-weight:700;margin-bottom:var(--v2-space-3)}
.v2-section .lead{color:var(--v2-ink-muted);font-size:13px;margin-bottom:var(--v2-space-3)}
</style>
