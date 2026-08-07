<template>
  <PageShell
    title="系统监控"
    sub-title="CPU / 内存 / 磁盘 / Redis / 队列实时状态 · 每 5 秒自动刷新"
    :loading="isLoading"
  >
  <div class="monitor">
    <!-- 空态 -->
    <EmptyState
      v-if="!metrics"
      :icon="MonitorIcon"
      title="暂无监控数据"
      :description="emptyMsg"
      tone="primary"
    />

    <template v-else>
      <!-- ═══ 顶部深色 hero ═══ -->
      <div class="hero">
        <div class="hero-top">
          <div class="hero-brand">
            <span class="hero-live" aria-label="系统状态运行中"></span>
            <span class="hero-title">系统运行中</span>
          </div>
          <div class="hero-meta">
            <span class="hero-time">数据时间 {{ formatTime(metrics.ts) }}</span>
            <span class="hero-countdown">下次刷新 {{ countdown }}s</span>
            <el-button
              class="hero-refresh"
              :icon="RefreshCw"
              :loading="refreshing"
              circle
              @click="load(true)"
            />
          </div>
        </div>

        <div class="hero-stats">
          <div class="hero-stat">
            <div class="hero-stat-num">
              <CountUp :value="cpuPercent" :decimals="0" suffix="%" :class="loadLevelClass(metrics.load_1)" />
            </div>
            <div class="hero-stat-label">CPU 负载（1 分钟）</div>
          </div>
          <div class="hero-stat">
            <div class="hero-stat-num">
              <CountUp :value="memPercent" suffix="%" :class="levelClass(memPercent)" />
            </div>
            <div class="hero-stat-label">内存占用</div>
          </div>
          <div class="hero-stat">
            <div class="hero-stat-num">
              <CountUp :value="diskPercent" suffix="%" :class="levelClass(diskPercent)" />
            </div>
            <div class="hero-stat-label">磁盘占用</div>
          </div>
        </div>
      </div>

      <!-- ═══ CPU 负载 ═══ -->
      <div class="metric-card">
        <div class="card-head">
          <span class="card-title">CPU 负载</span>
          <span class="card-sub">负载 / 核数 = 利用率 · {{ metrics.cpu_count }} 核</span>
        </div>
        <div class="load-row">
          <div v-for="item in loadItems" :key="item.key" class="load-item" :class="item.levelClass">
            <div class="load-num">
              <CountUp :value="item.value" :decimals="1" />
            </div>
            <div class="load-label">{{ item.label }}</div>
            <div class="load-track">
              <div class="load-fill" :class="item.levelClass" :style="{ width: item.barWidth }" />
            </div>
          </div>
        </div>
      </div>

      <!-- ═══ 内存 / 磁盘 / Redis 三列 ═══ -->
      <div class="grid-3">
        <div v-if="metrics.memory?.mem_total" class="metric-card">
          <div class="card-head">
            <span class="card-title">内存</span>
            <span class="card-sub">{{ formatBytes(metrics.memory.mem_used) }} / {{ formatBytes(metrics.memory.mem_total) }}</span>
          </div>
          <div class="gauge-wrap">
            <GaugeBar :percent="memPercent" />
          </div>
          <div class="bar-meta">
            <span>已用 <b>{{ formatBytes(metrics.memory.mem_used) }}</b></span>
            <span>可用 <b>{{ formatBytes(metrics.memory.mem_total - metrics.memory.mem_used) }}</b></span>
          </div>
        </div>

        <div v-if="metrics.disk?.disk_total" class="metric-card">
          <div class="card-head">
            <span class="card-title">磁盘</span>
            <span class="card-sub">{{ formatBytes(metrics.disk.disk_used) }} / {{ formatBytes(metrics.disk.disk_total) }}</span>
          </div>
          <div class="gauge-wrap">
            <GaugeBar :percent="diskPercent" />
          </div>
          <div class="bar-meta">
            <span>已用 <b>{{ formatBytes(metrics.disk.disk_used) }}</b></span>
            <span>可用 <b>{{ formatBytes(metrics.disk.disk_total - metrics.disk.disk_used) }}</b></span>
          </div>
        </div>

        <div class="metric-card redis-card">
          <div class="card-head">
            <span class="card-title">Redis</span>
            <span class="card-sub">内存占用</span>
          </div>
          <div class="redis-body">
            <div class="redis-big">
              <CountUp :value="redisMb" :decimals="1" />
              <span class="redis-unit">MB</span>
            </div>
            <div class="redis-detail">{{ formatBytes(metrics.redis?.redis_used_memory) }}</div>
          </div>
        </div>
      </div>

      <!-- ═══ 队列深度 ═══ -->
      <div class="metric-card">
        <div class="card-head">
          <span class="card-title">队列深度</span>
          <span class="card-sub">有积压的任务会在此亮起琥珀色</span>
        </div>
        <div class="queue-grid">
          <div v-for="q in queueCards" :key="q.key" class="queue-item" :class="{ 'has-job': queueDepth(q.key) > 0 }">
            <span class="queue-dot" :class="{ active: queueDepth(q.key) > 0 }"></span>
            <span class="queue-name">{{ q.label }}</span>
            <span class="queue-val">
              <CountUp :value="queueDepth(q.key)" />
            </span>
          </div>
        </div>
      </div>
    </template>
  </div>
  </PageShell>
</template>

<script setup lang="ts">
defineOptions({ name: 'Monitor' })
import { computed, ref, watch, onMounted, onBeforeUnmount } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { RefreshCw, Monitor as MonitorIcon } from 'lucide-vue-next'
import { get } from '@/api/request'
import CountUp from '@/components/CountUp.vue'
import GaugeBar from '@/components/GaugeBar.vue'
import PageShell from '@/components/PageShell/index.vue'
import EmptyState from '@/components/EmptyState/index.vue'

// F1 · TanStack Query 轮询：5s 拉取监控指标，刷新不闪烁（refetchInterval 由库接管）
const { data, isLoading, refetch } = useQuery({
  queryKey: ['monitor'],
  queryFn: () => get<any>('/admin/monitor/metrics'),
  refetchInterval: 5000,
})

const emptyMsg = ref('暂无数据，等待监控任务采集（每 60 秒）')
const refreshing = ref(false)
const countdown = ref(5)

// 数据可能带 empty 标记（监控任务还没采集到首帧）
const metrics = computed<any | null>(() => {
  const d = data.value
  if (d?.empty) return null
  return d
})

watch(data, (d) => {
  if (d?.empty) emptyMsg.value = d.msg || emptyMsg.value
})

// 每次成功刷新后重置倒计时（纯展示计时，不驱动数据拉取）
watch(data, () => { countdown.value = 5 })

const queueCards = [
  { key: 'default', label: '默认' },
  { key: 'notify', label: '通知' },
  { key: 'export', label: '导出' },
  { key: 'task', label: '任务' },
  { key: 'processing', label: '处理中' },
  { key: 'delayed', label: '延迟' },
  { key: 'dead', label: '失败队列' },
]

// ── 计算 ──
const memPercent = computed(() => metrics.value?.memory?.mem_used_percent ?? 0)
const diskPercent = computed(() => metrics.value?.disk?.disk_used_percent ?? 0)
const cpuPercent = computed(() => {
  const cores = metrics.value?.cpu_count || 1
  return Math.min(100, Math.round((metrics.value?.load_1 ?? 0) / cores * 100))
})
const redisMb = computed(() =>
  metrics.value?.redis?.redis_used_memory ? metrics.value.redis.redis_used_memory / 1024 / 1024 : 0,
)

const loadItems = computed(() => {
  const cores = metrics.value?.cpu_count || 1
  const mk = (key: string, label: string, value: number) => {
    const ratio = value / cores
    const levelClass = ratio > 0.9 ? 'load-high' : ratio > 0.5 ? 'load-mid' : 'load-low'
    return { key, label, value, levelClass, barWidth: `${Math.min(100, Math.round(ratio * 100))}%` }
  }
  const m = metrics.value ?? {}
  return [
    mk('l1', '1 分钟', m.load_1 ?? 0),
    mk('l5', '5 分钟', m.load_5 ?? 0),
    mk('l15', '15 分钟', m.load_15 ?? 0),
    mk('cores', '核数', cores),
  ]
})

const queueDepth = (key: string) => metrics.value?.queues?.[key] ?? 0

function levelClass(p: number) {
  if (p > 90) return 'is-danger'
  if (p > 70) return 'is-warning'
  return 'is-success'
}
const loadLevelClass = (v: number) => {
  const cores = metrics.value?.cpu_count || 1
  if (v > cores * 0.9) return 'is-danger'
  if (v > cores * 0.5) return 'is-warning'
  return 'is-success'
}

// ── 工具 ──
function formatBytes(n: number) {
  if (!n && n !== 0) return '—'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`
}

function formatTime(epoch: number) {
  if (!epoch) return '—'
  return new Date(epoch * 1000).toLocaleString()
}

// ── 手动刷新（轮询交给 TanStack Query 的 refetchInterval）──
async function load(manual = false) {
  if (manual) refreshing.value = true
  try {
    await refetch()
  } catch { /* 刷新失败静默处理 */ }
  finally {
    if (manual) refreshing.value = false
  }
}

// ── 倒计时展示计时器（轻量，只动数字，不拉数据）──
let cdTimer: number | undefined
onMounted(() => {
  cdTimer = window.setInterval(() => {
    countdown.value = Math.max(0, countdown.value - 1)
  }, 1000)
})
onBeforeUnmount(() => {
  if (cdTimer) clearInterval(cdTimer)
})
</script>

<style scoped lang="scss">
.monitor {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* ── 深色 hero ── */
.hero {
  background: var(--bg-sidebar);
  border-radius: var(--radius);
  padding: 24px;
  animation: hero-in 500ms var(--transition-base) both;
}
@keyframes hero-in {
  from { opacity: 0; }
  to { opacity: 1; }
}

.hero-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 24px;
}
.hero-brand {
  display: flex;
  align-items: center;
  gap: 10px;
}
.hero-live {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--success);
  animation: pulse 2s ease-in-out infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.35; }
}
.hero-title {
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--text-inverse);
}
.hero-meta {
  display: flex;
  align-items: center;
  gap: 16px;
}
.hero-time,
.hero-countdown {
  font-size: var(--text-xs);
  color: var(--text-sidebar);
  font-variant-numeric: tabular-nums;
}
.hero-refresh {
  --el-button-bg-color: rgba(255, 255, 255, 0.06);
  --el-button-border-color: transparent;
  --el-button-hover-bg-color: rgba(255, 255, 255, 0.12);
  --el-button-text-color: #fff;
  --el-button-hover-text-color: #fff;
}

.hero-stats {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
}
.hero-stat {
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: var(--radius);
  padding: 16px;
  text-align: center;
}
.hero-stat-num {
  font-size: 32px;
  font-weight: 700;
  color: var(--text-inverse);
  font-variant-numeric: tabular-nums;
}
.hero-stat-num .is-danger { color: var(--danger); }
.hero-stat-num .is-warning { color: var(--warning); }
.hero-stat-num .is-success { color: var(--success); }
.hero-stat-label {
  margin-top: 4px;
  font-size: var(--text-xs);
  color: var(--text-sidebar);
}

/* ── 通用卡片 ── */
.metric-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 20px;
  animation: card-in 400ms var(--transition-base) both;
}
.metric-card:nth-child(2) { animation-delay: 80ms; }
.metric-card:nth-child(3) { animation-delay: 160ms; }
.metric-card:nth-child(4) { animation-delay: 240ms; }
.metric-card:nth-child(5) { animation-delay: 320ms; }
@keyframes card-in {
  from { opacity: 0; }
  to { opacity: 1; }
}

.card-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 16px;
}
.card-title {
  font-size: var(--text-base);
  font-weight: 600;
  color: var(--text-primary);
}
.card-sub {
  font-size: var(--text-xs);
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
}

/* ── CPU 负载 ── */
.load-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
}
.load-item {
  text-align: center;
  padding: 16px 12px;
  background: var(--bg-input);
  border: 1px solid var(--border);
  border-radius: var(--radius);
}
.load-num {
  font-size: 28px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
.load-low  .load-num { color: var(--success); }
.load-mid  .load-num { color: var(--warning); }
.load-high .load-num { color: var(--danger); }
.load-label {
  margin-top: 4px;
  font-size: var(--text-xs);
  color: var(--text-secondary);
}
.load-track {
  margin-top: 12px;
  height: 4px;
  background: var(--border-light);
  border-radius: var(--radius-sm);
  overflow: hidden;
}
.load-fill {
  height: 100%;
  border-radius: var(--radius-sm);
  transition: width 800ms var(--transition-base);
}
.load-fill.load-low  { background: var(--success); }
.load-fill.load-mid  { background: var(--warning); }
.load-fill.load-high { background: var(--danger); }

/* ── 三列网格 ── */
.grid-3 {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
}
.gauge-wrap {
  margin: 8px 0 16px;
}
.bar-meta {
  display: flex;
  justify-content: space-between;
  font-size: var(--text-xs);
  color: var(--text-secondary);
}
.bar-meta b {
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
}

/* ── Redis ── */
.redis-card .card-head { margin-bottom: 8px; }
.redis-body {
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding-top: 16px;
}
.redis-big {
  font-size: 34px;
  font-weight: 700;
  color: var(--primary);
  font-variant-numeric: tabular-nums;
  display: flex;
  align-items: baseline;
  gap: 4px;
}
.redis-unit {
  font-size: var(--text-sm);
  color: var(--text-secondary);
  font-weight: 500;
}
.redis-detail {
  font-size: var(--text-xs);
  color: var(--text-secondary);
  margin-left: auto;
}

/* ── 队列 ── */
.queue-grid {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  gap: 12px;
}
.queue-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 16px 8px;
  background: var(--bg-input);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  transition: background var(--transition-fast), border-color var(--transition-fast);
}
.queue-item.has-job {
  background: var(--warning-bg);
  border-color: var(--warning);
}
.queue-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--border-dark);
  transition: background var(--transition-fast);
}
.queue-dot.active {
  background: var(--warning);
  animation: pulse 1.6s ease-in-out infinite;
}
.queue-name {
  font-size: var(--text-xs);
  color: var(--text-secondary);
}
.queue-val {
  font-size: 24px;
  font-weight: 700;
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
  transition: color var(--transition-fast);
}
.queue-item.has-job .queue-val { color: var(--warning); }
.queue-item.has-job .queue-name { color: var(--warning); }

/* ── 响应式 ── */
@media (max-width: 1200px) {
  .grid-3 { grid-template-columns: 1fr 1fr; }
  .queue-grid { grid-template-columns: repeat(4, 1fr); }
  .load-row { grid-template-columns: repeat(2, 1fr); }
}
@media (max-width: 720px) {
  .grid-3 { grid-template-columns: 1fr; }
  .queue-grid { grid-template-columns: repeat(2, 1fr); }
  .hero-stats { grid-template-columns: 1fr; }
  .hero-top { flex-direction: column; gap: 12px; align-items: flex-start; }
}
</style>
