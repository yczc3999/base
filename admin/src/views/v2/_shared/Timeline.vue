<script setup lang="ts">
/** WP-07B 合并时间线：submission/info/gate/decision/execution。items:[{kind,title,detail,ts,id}] */
defineProps<{ items: { kind: string; title: string; detail?: string; ts: string; id: string }[] }>()
</script>
<template>
  <div class="timeline">
    <div v-for="it in items" :key="it.id" class="tl-item" :class="it.kind">
      <div class="t">{{ it.title }}</div>
      <div v-if="it.detail" class="d">{{ it.detail }}</div>
      <div class="ts">{{ it.ts }} · {{ it.id }}</div>
    </div>
  </div>
</template>
<style scoped>
.timeline{border-left:2px solid var(--v2-line);margin-left:6px;padding-left:var(--v2-space-4)}
.tl-item{position:relative;padding:0 0 var(--v2-space-4) var(--v2-space-3)}
.tl-item::before{content:"";position:absolute;left:-7px;top:4px;width:10px;height:10px;border-radius:var(--v2-radius-full);background:var(--v2-primary);border:2px solid var(--v2-surface)}
.tl-item.gate::before{background:var(--v2-primary-block)}
.tl-item.info::before{background:var(--v2-info)}
.tl-item.exec::before{background:var(--v2-success)}
.tl-item .t{font-weight:600;font-size:13px}
.tl-item .d{color:var(--v2-ink-muted);font-size:12px;margin-top:2px}
.tl-item .ts{font-family:var(--v2-font-mono);font-size:11.5px;color:var(--v2-ink-muted)}
</style>
