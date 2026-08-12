<script setup lang="ts">
/** WP-07B 统一状态层：loading/empty/error/denied/partial 五态切换，无布局跳动。 */
defineProps<{
  loading: boolean
  error: string | null
  denied: boolean
  empty: boolean
  partial?: string | null
  retryable?: boolean
}>()
const emit = defineEmits<{ (e: 'retry'): void }>()
</script>
<template>
  <div class="v2-pagestate">
    <!-- loading 骨架（不闪回旧数据） -->
    <div v-if="loading" class="skel" data-testid="v2-loading">
      <div class="sk row-w60"></div><div class="sk row-w90"></div><div class="sk row-w75"></div>
      <div class="sk row-w80"></div><div class="sk row-w50"></div>
    </div>
    <!-- denied -->
    <div v-else-if="denied" class="panel-denied" data-testid="v2-denied">
      <p class="t">无权限查看此数据</p>
      <p class="m">需要对应的 <span class="mono">v2:*:view</span> 权限，请联系管理员。</p>
    </div>
    <!-- error -->
    <div v-else-if="error" class="panel-error" data-testid="v2-error">
      <p class="t">请求失败：{{ error }}</p>
      <p class="m">请稍后重试，或查看 Integrity &gt; Alerts。</p>
      <button v-if="retryable !== false" class="retry" type="button" @click="emit('retry')">重试</button>
    </div>
    <div v-else>
      <!-- partial 是正文的附加提示，不得把正文替换掉。 -->
      <div v-if="partial" class="partial-note" data-testid="v2-partial">{{ partial }}</div>
      <!-- empty -->
      <div v-if="empty" class="panel-empty" data-testid="v2-empty">
        <p class="t">暂无数据</p>
        <p class="m">调整筛选条件后重试。</p>
      </div>
      <!-- 默认槽：正文 -->
      <slot v-else />
    </div>
  </div>
</template>
<style scoped>
.v2-pagestate{min-height:160px}
.skel .sk{height:16px;background:#EDE8DC;border-radius:var(--v2-radius-sm);margin-bottom:var(--v2-space-3)}
.row-w60{width:60%}.row-w90{width:90%}.row-w75{width:75%}.row-w80{width:80%}.row-w50{width:50%}
.panel-denied,.panel-empty{border:var(--v2-border-w) solid var(--v2-line);background:#F4F0E6;border-radius:var(--v2-radius-md);padding:var(--v2-space-8);text-align:center}
.panel-error{border:var(--v2-border-w) solid var(--v2-danger);background:var(--v2-danger-soft);border-radius:var(--v2-radius-md);padding:var(--v2-space-4)}
.panel-denied .t,.panel-error .t,.panel-empty .t{font-weight:700;margin-bottom:var(--v2-space-2)}
.panel-denied .m,.panel-error .m,.panel-empty .m{color:var(--v2-ink-muted)}
.partial-note{border-left:3px solid var(--v2-warning);background:var(--v2-warning-soft);padding:var(--v2-space-2) var(--v2-space-3);border-radius:0 var(--v2-radius-sm) var(--v2-radius-sm) 0;font-size:12.5px;color:var(--v2-warning);margin-bottom:var(--v2-space-3)}
.mono{font-family:var(--v2-font-mono)}
.retry{margin-top:var(--v2-space-3);min-height:var(--v2-control-h);padding:0 var(--v2-space-3);border:1px solid var(--v2-danger);border-radius:var(--v2-radius-sm);background:var(--v2-surface);color:var(--v2-danger);cursor:pointer}
</style>
