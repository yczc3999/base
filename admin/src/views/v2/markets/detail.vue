<script setup lang="ts">
/** WP-07B Market Detail：market + snapshot/specs/current/cohort 查询链。 */
import { computed, toRef } from 'vue'
import { useRoute } from 'vue-router'
import PageShell from '@/components/PageShell/index.vue'
import { ArtifactLink, PageState, DetailSection, KeyValueGrid } from '../_shared'
import { useMarket } from '@/queries/v2/markets'

const route = useRoute()
const id = toRef(route.params, 'id') as unknown as import('vue').Ref<string>
const { data, isLoading, isError, displayError, denied, refetch } = useMarket(id)
const m = computed(() => data.value?.market ?? null)
</script>
<template>
  <PageShell class="v2-page" :title="m?.question ?? 'Market Detail'" :loading="isLoading" sub-title="market · snapshot · spec · cohort">
    <PageState
:loading="isLoading"
:error="displayError" :denied="denied" :empty="!isLoading && !isError && !m"
      @retry="() => refetch()">
      <div v-if="m" class="grid2">
        <DetailSection title="Market">
          <KeyValueGrid
:rows="[
            { k: 'id', v: m.id, mono: true },
            { k: 'slug', v: m.slug ?? '-' },
            { k: 'neg_risk', v: m.neg_risk ? '是' : '否' },
            { k: '状态', v: m.closed ? 'CLOSED' : 'OPEN' },
            { k: 'volume / liquidity', v: `${m.volume} / ${m.liquidity}`, mono: true },
          ]" />
          <p class="artifact-row">raw artifact <ArtifactLink :content-hash="m.raw_artifact_ref" /></p>
        </DetailSection>
        <DetailSection title="Snapshot / Current">
          <p class="mono">{{ JSON.stringify(data?.snapshot ?? null) }}</p>
          <p class="mono">{{ JSON.stringify(data?.current ?? null) }}</p>
        </DetailSection>
      </div>
      <DetailSection v-if="data?.specs?.length" title="Specs">
        <table class="mini">
          <tbody>
            <tr v-for="(s,i) in data.specs" :key="i">
              <td class="mono">{{ s.contract_key }}</td><td><ArtifactLink :content-hash="String(s.content_hash ?? '')" /></td><td>{{ s.status }}</td>
            </tr>
          </tbody>
        </table>
      </DetailSection>
      <DetailSection v-if="data?.cohort?.length" title="Cohort">
        <p class="mono">{{ JSON.stringify(data.cohort) }}</p>
      </DetailSection>
    </PageState>
  </PageShell>
</template>
<style scoped>
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:var(--v2-space-4)}
@media (max-width:860px){.grid2{grid-template-columns:1fr}}
.mini{border-collapse:collapse;width:100%}.mini td{border-bottom:1px solid var(--v2-line);padding:4px 8px;font-size:12.5px}
.mono{font-family:var(--v2-font-mono);word-break:break-all}
.artifact-row{display:flex;justify-content:space-between;gap:var(--v2-space-3);margin-top:var(--v2-space-3);color:var(--v2-ink-muted);font-size:12.5px}
</style>
