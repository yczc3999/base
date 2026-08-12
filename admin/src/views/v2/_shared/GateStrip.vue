<script setup lang="ts">
/** WP-07B Gate 条带：PASS/FAIL/NOT_RUN 均有文本。gates: [{name,result}] */
defineProps<{ gates: { name: string; result: string | null }[] }>()
</script>
<template>
  <div v-if="gates.length" class="gates">
    <div v-for="g in gates" :key="g.name" class="gate" :class="tone(g.result)">
      <div class="g">{{ g.name }}</div>
      <div class="st">{{ g.result ?? 'NOT_RUN' }}</div>
    </div>
  </div>
</template>
<script lang="ts">
export default {
  methods: { tone(r: string | null) { return r === 'PASS' ? 'pass' : r === 'FAIL' ? 'fail' : 'notrun' } },
}
</script>
<style scoped>
.gates{display:grid;grid-template-columns:repeat(8,1fr);gap:var(--v2-space-2)}
.gate{border:var(--v2-border-w) solid var(--v2-line);border-radius:var(--v2-radius-sm);padding:var(--v2-space-2);text-align:center;background:#FBF8F0}
.gate .g{font-weight:700;font-size:12px}
.gate .st{font-size:11px;margin-top:2px;display:block}
.gate.pass{border-color:var(--v2-success);background:var(--v2-success-soft);color:var(--v2-success)}
.gate.fail{border-color:var(--v2-danger);background:var(--v2-danger-soft);color:var(--v2-danger)}
.gate.notrun{color:var(--v2-ink-muted)}
@media (max-width:860px){.gates{grid-template-columns:repeat(4,1fr)}}
@media (max-width:480px){.gates{grid-template-columns:repeat(2,1fr)}}
</style>
