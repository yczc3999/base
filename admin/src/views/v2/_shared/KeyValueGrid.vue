<script setup lang="ts">
/** WP-07B 键值网格：rows:[{k,v,mono?,link?}]。 */
defineProps<{ rows: { k: string; v: string; mono?: boolean; link?: boolean }[] }>()
</script>
<template>
  <div class="v2-kvg">
    <div v-for="(r,i) in rows" :key="i" class="kv">
      <span class="k">{{ r.k }}</span>
      <span class="v" :class="{ mono: r.mono }">
        <a v-if="r.link" href="#" class="lnk" @click.prevent>{{ r.v }}</a>
        <template v-else>{{ r.v }}</template>
      </span>
    </div>
  </div>
</template>
<style scoped>
.v2-kvg .kv{display:flex;justify-content:space-between;gap:var(--v2-space-4);padding:var(--v2-space-2) 0;border-bottom:var(--v2-border-w) solid var(--v2-line)}
.v2-kvg .kv:last-child{border-bottom:none}
.k{color:var(--v2-ink-muted);font-size:12.5px;flex:none}
.v{text-align:right;word-break:break-all;overflow-wrap:anywhere;min-width:0}
.mono{font-family:var(--v2-font-mono);font-size:12.5px}
.lnk{color:var(--v2-primary);text-decoration:underline}
</style>
