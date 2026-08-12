<script setup lang="ts">
/** WP-07B Dashboard：只读 WP-04 五张 projection（不扫事实大表）。 */
import { computed } from 'vue'
import PageShell from '@/components/PageShell/index.vue'
import { PageState, DetailSection } from '../_shared'
import { useDashboard } from '@/queries/v2/dashboard'
import type { DashboardBlock } from '@/api/v2/types'

const { data, isLoading, isError, error } = useDashboard()
const blocks = computed<Record<string, DashboardBlock>>(() => data.value?.blocks ?? {})
const names = ['ops_health_current', 'pipeline_funnel_hourly', 'account_risk_current',
  'provider_cost_daily', 'latest_chain_summary']
</script>
<template>
  <PageShell title="Dashboard" :loading="isLoading" sub-title="系统健康 · 资金风险 · 最近完整链">
    <PageState
:loading="isLoading" :error="isError ? String(error) : null" :denied="false"
      :empty="!isLoading && !isError && !Object.keys(blocks).length">
      <div class="grid2">
        <DetailSection v-for="n in names" :key="n" :title="n">
          <p class="muted">{{ blocks[n]?.freshness_status ?? 'missing' }} · as_of {{ blocks[n]?.as_of }}</p>
          <table v-if="blocks[n]?.rows?.length" class="mini">
            <tbody>
              <tr v-for="(r,i) in blocks[n].rows.slice(0,8)" :key="i">
                <td v-for="(val,key) in r" :key="key" class="mono">{{ val }}</td>
              </tr>
            </tbody>
          </table>
          <p v-else class="muted">（空 projection）</p>
        </DetailSection>
      </div>
    </PageState>
  </PageShell>
</template>
<style scoped>
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:var(--v2-space-4)}
@media (max-width:860px){.grid2{grid-template-columns:1fr}}
.mono{font-family:var(--v2-font-mono);font-size:11.5px}
.mini{border-collapse:collapse;width:100%}.mini td{border-bottom:1px solid var(--v2-line);padding:2px 6px;font-size:11.5px;word-break:break-all}
.muted{color:var(--v2-ink-muted);font-size:12.5px}
</style>
