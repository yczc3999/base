<script setup lang="ts" generic="T extends { id: string }">
/** WP-07B keyset 表格：next_cursor/has_more 翻页；改变 limit 不改变 snapshot。 */
defineProps<{
  rows: T[]
  loading: boolean
  hasMore: boolean
  limit: number
  total?: number
}>()
const emit = defineEmits<{ (e:'next'): void; (e:'limit', n:number): void }>()
</script>
<template>
  <div class="v2-kt">
    <slot name="columns" :rows="rows" />
    <div class="pager">
      <span class="muted">{{ rows.length }} 条（keyset，非 total）</span>
      <button :disabled="!hasMore || loading" class="link-btn" @click="emit('next')">下一页 ›</button>
    </div>
  </div>
</template>
<style scoped>
.v2-kt .pager{display:flex;justify-content:space-between;align-items:center;margin-top:var(--v2-space-3)}
.link-btn{background:none;border:none;color:var(--v2-primary);text-decoration:underline;cursor:pointer;height:var(--v2-control-h)}
.link-btn:disabled{color:var(--v2-ink-muted);cursor:not-allowed;text-decoration:none}
.muted{color:var(--v2-ink-muted);font-size:12.5px}
</style>
