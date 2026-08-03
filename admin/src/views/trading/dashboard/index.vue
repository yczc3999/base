<template>
  <div v-loading="loading">
    <!-- 统计卡 -->
    <div class="stat-cards">
      <div class="stat-card">
        <div class="stat-num">{{ stats?.today?.signals ?? 0 }}</div>
        <div class="stat-label">今日信号</div>
      </div>
      <div class="stat-card">
        <div class="stat-num">{{ stats?.today?.trades ?? 0 }}</div>
        <div class="stat-label">今日交易</div>
      </div>
      <div class="stat-card">
        <div class="stat-num">{{ fmtRate(stats?.today?.win_rate) }}</div>
        <div class="stat-label">今日胜率</div>
      </div>
      <div class="stat-card" :class="pnlClass(stats?.today?.pnl)">
        <div class="stat-num">{{ fmtPnl(stats?.today?.pnl) }}</div>
        <div class="stat-label">今日 PnL($)</div>
      </div>
      <div class="stat-card" :class="pnlClass(stats?.total?.pnl)">
        <div class="stat-num">{{ fmtPnl(stats?.total?.pnl) }}</div>
        <div class="stat-label">累计 PnL($)</div>
      </div>
      <div class="stat-card" :class="stats?.breaker?.triggered ? 'loss' : 'ok'">
        <div class="stat-num breaker">
          {{ stats?.breaker?.triggered ? '已熔断' : '正常' }}
        </div>
        <div class="stat-label">
          熔断状态（线 {{ stats?.breaker?.daily_loss_breaker_usd ?? '-' }}$）
        </div>
      </div>
    </div>

    <!-- 累计 PnL 曲线 -->
    <el-card class="chart-card">
      <template #header>
        <div class="card-header">
          <span>资金曲线（累计 PnL）</span>
          <span class="mode-info">
            模式 <el-tag size="small" effect="dark"
              :type="stats?.mode === 'LIVE' ? 'danger' : stats?.mode === 'SHADOW' ? 'warning' : 'info'">
              {{ stats?.mode || '-' }}
            </el-tag>
            持仓 {{ stats?.open_positions ?? 0 }}
          </span>
        </div>
      </template>
      <div ref="chartRef" class="equity-chart"></div>
      <el-empty v-if="!loading && !equityPoints.length" description="暂无平仓交易，等待 bot 产生 round-trip" :image-size="80" />
    </el-card>

    <!-- 最新心跳 -->
    <el-card class="hb-card">
      <template #header><span>最新心跳</span></template>
      <div v-if="hb" class="hb-grid">
        <div class="hb-item"><span class="hb-label">链上时间</span><span>{{ fmtTime(hb.ts) }}</span></div>
        <div class="hb-item"><span class="hb-label">区块</span><span>{{ hb.block }}</span></div>
        <div class="hb-item"><span class="hb-label">监听池数</span><span>{{ hb.pools }}</span></div>
        <div class="hb-item"><span class="hb-label">开放仓位</span><span>{{ hb.open_count }}</span></div>
        <div class="hb-item"><span class="hb-label">信号累计</span><span>{{ hb.signals_total }}</span></div>
        <div class="hb-item"><span class="hb-label">交易累计</span><span>{{ hb.trades_total }}</span></div>
        <div class="hb-item"><span class="hb-label">bot 累计 PnL</span>
          <span :style="{ color: Number(hb.cum_pnl_usd) >= 0 ? 'var(--el-color-success)' : 'var(--el-color-danger)' }">
            {{ fmtPnl(hb.cum_pnl_usd) }}
          </span>
        </div>
        <div class="hb-item"><span class="hb-label">落库时间</span><span>{{ fmtTime(hb.created_at) }}</span></div>
      </div>
      <el-empty v-else description="尚未收到心跳（桥接任务 15s 轮询 run.log）" :image-size="60" />
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { get } from '@/api/request'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, MarkLineComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([LineChart, GridComponent, TooltipComponent, MarkLineComponent, CanvasRenderer])

const loading = ref(false)
const stats = ref<any>(null)
const equityPoints = ref<any[]>([])
const chartRef = ref<HTMLElement>()
let chart: echarts.ECharts | null = null

const hb = computed(() => stats.value?.latest_heartbeat)

function fmtPnl(v: any) {
  const n = Number(v || 0)
  return (n >= 0 ? '+' : '') + n.toFixed(2)
}
function fmtRate(v: any) {
  return v == null ? '-' : `${(Number(v) * 100).toFixed(1)}%`
}
function fmtTime(v: any) {
  return v ? String(v).replace('T', ' ').slice(0, 19) : '-'
}
function pnlClass(v: any) {
  return Number(v || 0) >= 0 ? 'profit' : 'loss'
}

function renderChart() {
  if (!chartRef.value) return
  if (!chart) chart = echarts.init(chartRef.value)
  const xs = equityPoints.value.map(p => fmtTime(p.ts))
  const ys = equityPoints.value.map(p => p.cum_pnl)
  chart.setOption({
    grid: { left: 60, right: 20, top: 30, bottom: 40 },
    tooltip: {
      trigger: 'axis',
      formatter: (ps: any[]) => {
        const p = ps[0]
        const pt = equityPoints.value[p.dataIndex]
        return `${pt.ts}<br/>累计 PnL: ${fmtPnl(pt.cum_pnl)}$<br/>本笔: ${fmtPnl(pt.pnl)}$ ${pt.symbol || ''}`
      },
    },
    xAxis: { type: 'category', data: xs, boundaryGap: false },
    yAxis: { type: 'value', name: 'USD', scale: true },
    series: [{
      name: '累计 PnL',
      type: 'line',
      data: ys,
      smooth: true,
      showSymbol: false,
      areaStyle: { opacity: 0.12 },
      lineStyle: { width: 2 },
      markLine: {
        silent: true,
        symbol: 'none',
        lineStyle: { type: 'dashed', color: '#94a3b8' },
        data: [{ yAxis: 0 }],
      },
    }],
  }, true)
}

async function load() {
  loading.value = true
  try {
    const [s, e] = await Promise.all([
      get('/admin/trading/dashboard/stats'),
      get('/admin/trading/dashboard/equity'),
    ])
    stats.value = s
    equityPoints.value = e?.points || []
    await nextTick()
    renderChart()
  } finally {
    loading.value = false
  }
}

function onResize() { chart?.resize() }

onMounted(() => {
  load()
  window.addEventListener('resize', onResize)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  chart?.dispose()
  chart = null
})
</script>

<style scoped>
.stat-cards { display: flex; gap: 12px; margin-bottom: 16px; }
.stat-card {
  flex: 1; padding: 16px; border-radius: 8px; text-align: center;
  border: 1px solid var(--el-border-color-lighter); background: var(--el-bg-color);
}
.stat-num { font-size: 26px; font-weight: 700; line-height: 1.2; }
.stat-num.breaker { font-size: 22px; }
.stat-label { font-size: 13px; color: var(--el-text-color-secondary); margin-top: 4px; }
.stat-card.profit .stat-num { color: var(--el-color-success); }
.stat-card.loss .stat-num { color: var(--el-color-danger); }
.stat-card.ok .stat-num { color: var(--el-color-success); }
.chart-card { margin-bottom: 16px; }
.card-header { display: flex; justify-content: space-between; align-items: center; }
.mode-info { display: flex; align-items: center; gap: 6px; font-size: 13px; font-weight: 400; color: var(--el-text-color-secondary); }
.equity-chart { height: 320px; }
.hb-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
.hb-item { display: flex; flex-direction: column; gap: 4px; font-size: 14px; }
.hb-label { font-size: 12px; color: var(--el-text-color-secondary); }
</style>
