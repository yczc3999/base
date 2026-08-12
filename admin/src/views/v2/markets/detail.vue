<script setup lang="ts">
/** WP-07B Market Detail：market + snapshot/specs/current/cohort 查询链。 */
import { computed, toRef } from 'vue'
import { useRoute } from 'vue-router'
import PageShell from '@/components/PageShell/index.vue'
import { PageState, DetailSection, KeyValueGrid } from '../_shared'
import { useMarket } from '@/queries/v2/markets'

const route = useRoute()
const id = toRef(route.params, 'id') as unknown as import('vue').Ref<string>
const { data, isLoading, isError, error } = useMarket(id)
const m = computed(() => data.value?.market ?? null)
</script>
<template>
  <PageShell :title="m?.question ?? 'Market Detail'" :loading="isLoading" sub-title="market · snapshot · spec · cohort">
    <PageState
:loading="isLoading" :error="isError ? String(error) : null" :denied="false"
      :empty="!isLoading && !isError && !m">
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
              <td class="mono">{{ s.contract_key }}</td><td class="mono">{{ s.content_hash }}</td><td>{{ s.status }}</td>
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
</style>
