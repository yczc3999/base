<script setup lang="ts">
/** WP-07B Dashboard：只读 WP-04 五张 projection（不扫事实大表）。 */
import { computed } from 'vue'
import PageShell from '@/components/PageShell/index.vue'
import { PageState } from '../_shared'
import { useDashboard } from '@/queries/v2/dashboard'
import type { DashboardBlock } from '@/api/v2/types'

const { data, isLoading, isError, displayError, denied, refetch } = useDashboard()
const blocks = computed<Record<string, DashboardBlock>>(() => data.value?.blocks ?? {})

const cards = [
  { key: 'ops_health_current', title: '系统健康', hint: '运行时与管道是否可用' },
  { key: 'account_risk_current', title: '资金风险', hint: '账户占用与限额' },
  { key: 'latest_chain_summary', title: '最近完整链', hint: '最近一次成功结算' },
  { key: 'provider_cost_daily', title: '今日成本', hint: '模型与检索费用' },
  { key: 'pipeline_funnel_hourly', title: '决策漏斗', hint: '本小时筛选与决策' },
] as const

const partialMessage = computed(() => {
  const degraded = cards
    .map((card) => card.key)
    .filter((name) => {
      const state = blocks.value[name]?.freshness_status
      return state === 'stale' || state === 'missing'
    })
  return degraded.length ? `部分投影已降级：${degraded.join(', ')}` : null
})

function freshnessLabel(status: string | undefined) {
  if (status === 'fresh') return '新鲜'
  if (status === 'stale') return '陈旧'
  return '缺失'
}

function firstValue(block: DashboardBlock | undefined) {
  const row = block?.rows?.[0]
  if (!row) return '—'
  const values = Object.values(row).filter((value) => value !== null && value !== '')
  return values.length ? String(values[0]) : '—'
}
</script>

<template>
  <PageShell class="v2-page" title="总览" :loading="isLoading" sub-title="健康 · 风险 · 完整链 · 成本 · 决策">
    <PageState
      :loading="isLoading"
      :error="displayError"
      :denied="denied"
      :partial="partialMessage"
      :empty="!isLoading && !isError && !Object.keys(blocks).length"
      @retry="() => refetch()"
    >
      <div class="hero">
        <div class="hero-left">
          <div class="hero-live" aria-label="系统状态"></div>
          <div>
            <div class="hero-title">交易引擎总览</div>
            <div class="hero-sub">只读投影 · as_of {{ data?.as_of ?? '—' }}</div>
          </div>
        </div>
      </div>

      <div class="stat-cards">
        <div v-for="card in cards" :key="card.key" class="schema-stat">
          <div class="schema-stat-info">
            <div class="schema-stat-value">{{ firstValue(blocks[card.key]) }}</div>
            <div class="schema-stat-label">{{ card.title }} · {{ freshnessLabel(blocks[card.key]?.freshness_status) }}</div>
            <div class="schema-stat-hint">{{ card.hint }}</div>
          </div>
        </div>
      </div>

      <div class="content-card">
        <div class="card-header"><span class="card-title">投影明细</span></div>
        <div class="card-body" style="padding:0">
          <table class="mini-table">
            <thead>
              <tr><th>投影</th><th>新鲜度</th><th>as_of</th><th>行数</th></tr>
            </thead>
            <tbody>
              <tr v-for="card in cards" :key="card.key">
                <td>{{ card.title }}</td>
                <td>{{ freshnessLabel(blocks[card.key]?.freshness_status) }}</td>
                <td>{{ blocks[card.key]?.as_of ?? '—' }}</td>
                <td>{{ blocks[card.key]?.rows?.length ?? 0 }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </PageState>
  </PageShell>
</template>

<style scoped>
.hero {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-lg);
  background: var(--bg-sidebar);
  color: #fff;
  border-radius: var(--radius);
}
.hero-left { display: flex; align-items: center; gap: var(--space-md); }
.hero-live {
  width: 10px; height: 10px; border-radius: 50%;
  background: var(--success); flex-shrink: 0;
}
.hero-title { font-size: var(--text-lg); font-weight: 600; }
.hero-sub { font-size: var(--text-xs); color: #94A3B8; margin-top: 4px; }
.stat-cards {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: var(--space-base);
  margin: var(--space-base) 0;
}
@media (max-width: 1200px) { .stat-cards { grid-template-columns: repeat(2, 1fr); } }
.schema-stat {
  padding: var(--space-base) var(--space-lg);
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-left: 3px solid var(--primary);
  border-radius: var(--radius);
}
.schema-stat-value {
  font-size: var(--text-xl);
  font-weight: 700;
  color: var(--text-primary);
  word-break: break-all;
}
.schema-stat-label { font-size: var(--text-xs); color: var(--text-secondary); margin-top: 6px; }
.schema-stat-hint { font-size: 12px; color: var(--text-secondary); margin-top: 2px; }
.content-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
}
.card-header { padding: var(--space-md) var(--space-lg); border-bottom: 1px solid var(--border); }
.card-title { font-weight: 600; }
.mini-table { width: 100%; border-collapse: collapse; }
.mini-table th, .mini-table td {
  text-align: left; padding: 10px 16px; border-bottom: 1px solid var(--border);
  font-size: var(--text-sm);
}
.mini-table th { color: var(--text-secondary); font-weight: 500; }
</style>
