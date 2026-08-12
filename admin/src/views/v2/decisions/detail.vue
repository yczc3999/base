<script setup lang="ts">
/** WP-07B Decision Detail：decision + quote/underwriting/action_sets/intents 链。 */
import { computed, toRef } from 'vue'
import { useRoute } from 'vue-router'
import PageShell from '@/components/PageShell/index.vue'
import { ArtifactLink, PageState, DetailSection, KeyValueGrid } from '../_shared'
import { useDecision } from '@/queries/v2/decisions'

const route = useRoute()
const id = toRef(route.params, 'id') as unknown as import('vue').Ref<string>
const { data, isLoading, isError, displayError, denied, refetch } = useDecision(id)
const d = computed(() => data.value?.decision ?? null)
</script>
<template>
  <PageShell class="v2-page" :title="d?.decision_key ?? 'Decision Detail'" :loading="isLoading" sub-title="decision · quote · action · intent">
    <PageState
:loading="isLoading"
:error="displayError" :denied="denied" :empty="!isLoading && !isError && !d"
      @retry="() => refetch()">
      <div v-if="d" class="grid2">
        <DetailSection title="Decision">
          <KeyValueGrid
:rows="[
            { k: 'id', v: d.id, mono: true },
            { k: 'class', v: d.decision_class },
            { k: 'status', v: d.status },
            { k: 'action', v: d.selected_action_type ?? '-' },
            { k: 'trigger_at', v: d.trigger_at ?? '-', mono: true },
          ]" />
        </DetailSection>
        <DetailSection title="Decision Artifacts">
          <p class="artifact-row">input <ArtifactLink :content-hash="d.input_hash" /></p>
          <p class="artifact-row">output <ArtifactLink :content-hash="d.output_hash" /></p>
        </DetailSection>
        <DetailSection title="Quote / Underwriting">
          <p class="mono">{{ JSON.stringify(data?.quote_bindings?.[0] ?? null) }}</p>
          <p class="mono">{{ JSON.stringify(data?.underwriting_plans?.[0] ?? null) }}</p>
        </DetailSection>
      </div>
      <DetailSection v-if="data?.action_sets?.length" title="Action Sets">
        <p class="mono">{{ JSON.stringify(data.action_sets) }}</p>
      </DetailSection>
      <DetailSection v-if="data?.intents?.length" title="Intents">
        <table class="mini">
          <tbody>
            <tr v-for="(it,i) in data.intents" :key="i">
              <td class="mono">{{ it.intent_key }}</td><td>{{ it.status }}</td><td class="mono">{{ it.ttl_at ?? '-' }}</td>
            </tr>
          </tbody>
        </table>
      </DetailSection>
    </PageState>
  </PageShell>
</template>
<style scoped>
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:var(--v2-space-4)}
@media (max-width:860px){.grid2{grid-template-columns:1fr}}
.mini{border-collapse:collapse;width:100%}.mini td{border-bottom:1px solid var(--v2-line);padding:4px 8px;font-size:12.5px}
.mono{font-family:var(--v2-font-mono);word-break:break-all}
.artifact-row{display:flex;justify-content:space-between;gap:var(--v2-space-3);margin-bottom:var(--v2-space-2);color:var(--v2-ink-muted);font-size:12.5px}
</style>
