<script setup lang="ts">
/** WP-07B Episode Detail：对应已确认高保真预览（identity/状态/Blind vs Market/Gate/Evidence/AI/决策/时间线/审计）。 */
import { computed, toRef, watchEffect } from 'vue'
import { useRoute } from 'vue-router'
import PageShell from '@/components/PageShell/index.vue'
import { ArtifactLink, PageState, StatusBadge, GateStrip, Timeline, DetailSection, KeyValueGrid } from '../_shared'
import { useEpisode } from '@/queries/v2/episodes'

const route = useRoute()
const id = toRef(route.params, 'id') as unknown as import('vue').Ref<string>
const { data, isLoading, isError, displayError, denied, refetch } = useEpisode(id)
const ep = computed(() => data.value?.episode ?? null)
const gates = computed(() => (data.value?.gates ?? []).map((g: any) => ({ name: g.gate, result: g.result })))
import { ref } from 'vue'
const timeline = ref<{ kind: string; title: string; detail?: string; ts: string; id: string }[]>([])
// 时间线：submission/gate/info snapshot（合并自 episode detail 的 submissions/gates）
watchEffect(() => {
  const items: any[] = []
  for (const s of data.value?.submissions ?? []) items.push({ kind: '', title: 'Submission', detail: s.submission_key, ts: s.committed_at ?? '', id: `s-${s.id}` })
  for (const g of data.value?.gates ?? []) items.push({ kind: 'gate', title: `Gate ${g.gate}`, detail: g.result, ts: g.committed_at ?? '', id: `g-${g.id}` })
  timeline.value = items.sort((a, b) => (a.ts < b.ts ? 1 : -1)).slice(0, 20)
})
</script>
<template>
  <PageShell class="v2-page" :title="ep?.episode_key ?? 'Episode Detail'" :loading="isLoading" sub-title="cognition episode · 下钻">
    <PageState
:loading="isLoading"
:error="displayError" :denied="denied" :empty="!isLoading && !isError && !ep"
      @retry="() => refetch()">
      <div v-if="ep" class="ep-head">
        <h1 class="q">{{ ep.trigger }} <StatusBadge :tone="ep.status === 'DECIDED' ? 'success' : 'info'">{{ ep.status }}</StatusBadge></h1>
        <p class="mono identity">episode {{ ep.episode_key }} · status {{ ep.status }} · cognition {{ ep.cognition_status }} · cutoff {{ ep.cutoff_at }}</p>
      </div>
      <div class="grid2">
        <DetailSection title="当前结论">
          <KeyValueGrid
:rows="ep ? [
            { k: 'status', v: ep.status },
            { k: 'cognition', v: ep.cognition_status },
            { k: 'cutoff_at', v: ep.cutoff_at ?? '-', mono: true },
            { k: 'horizon', v: ep.horizon ?? '-' },
          ] : []" />
        </DetailSection>
        <DetailSection title="决策摘要">
          <KeyValueGrid
:rows="ep ? [
            { k: 'decision_opportunity_id', v: ep.decision_opportunity_id ?? '-', mono: true },
            { k: 'objective_contract_id', v: ep.objective_contract_id ?? '-', mono: true },
            { k: 'drop_reason', v: ep.drop_reason ?? '-' },
          ] : []" />
        </DetailSection>
      </div>
      <DetailSection title="Gate 结果">
        <GateStrip :gates="gates" />
      </DetailSection>
      <DetailSection v-if="data?.priors?.length" title="Blind Prior">
        <table class="mini"><tbody>
          <tr v-for="(prior, index) in data.priors" :key="index">
            <td>{{ prior.reference_class }}</td><td>{{ prior.status }}</td>
            <td><ArtifactLink :content-hash="String(prior.content_hash ?? '')" /></td>
          </tr>
        </tbody></table>
      </DetailSection>
      <DetailSection title="Evidence / 提交">
        <table class="mini">
          <tbody>
            <tr v-for="(b,i) in (data?.evidence_bundles ?? []).slice(0,8)" :key="i">
              <td class="mono">{{ b.bundle_key }}</td><td>{{ b.status }}</td>
              <td><ArtifactLink :content-hash="String(b.bundle_hash ?? '')" /></td>
            </tr>
          </tbody>
        </table>
        <table v-if="data?.submissions?.length" class="mini submissions"><tbody>
          <tr v-for="(submission, index) in data.submissions" :key="index">
            <td class="mono">{{ submission.submission_key }}</td><td>{{ submission.status }}</td>
            <td><ArtifactLink :content-hash="String(submission.algorithm_hash ?? '')" /></td>
          </tr>
        </tbody></table>
      </DetailSection>
      <DetailSection title="发生过程（时间线）">
        <Timeline :items="timeline" />
      </DetailSection>
    </PageState>
  </PageShell>
</template>
<style scoped>
.ep-head h1.q{font-size:20px;font-weight:700;margin-bottom:var(--v2-space-2);overflow-wrap:anywhere}
.identity{color:var(--v2-ink-muted);font-size:12.5px;margin-bottom:var(--v2-space-4);word-break:break-all}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:var(--v2-space-4)}
@media (max-width:860px){.grid2{grid-template-columns:1fr}}
.mini{border-collapse:collapse;width:100%}.mini td{border-bottom:1px solid var(--v2-line);padding:4px 8px;font-size:12.5px}
.submissions{margin-top:var(--v2-space-3)}
.mono{font-family:var(--v2-font-mono)}
</style>
