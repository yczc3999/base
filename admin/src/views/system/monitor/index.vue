<template>
  <div class="monitor">
    <div class="monitor-header" v-if="metrics">
      <span class="header-title">系统资源</span>
      <span class="header-time">数据时间：{{ formatTime(metrics.ts) }} · 每 5s 自动刷新</span>
      <el-button :icon="Refresh" size="small" @click="load" circle />
    </div>

    <!-- 空态 -->
    <div v-if="!metrics" class="empty-state">
      <el-empty :description="emptyMsg" />
    </div>

    <template v-else>
      <!-- CPU -->
      <div class="metric-card">
        <div class="card-title">CPU 负载</div>
        <div class="load-row">
          <div class="load-item">
            <div class="load-num" :class="loadClass(metrics.load_1)">{{ metrics.load_1 }}</div>
            <div class="load-label">1 分钟</div>
          </div>
          <div class="load-item">
            <div class="load-num">{{ metrics.load_5 }}</div>
            <div class="load-label">5 分钟</div>
          </div>
          <div class="load-item">
            <div class="load-num">{{ metrics.load_15 }}</div>
            <div class="load-label">15 分钟</div>
          </div>
          <div class="load-item">
            <div class="load-num">{{ metrics.cpu_count }}</div>
            <div class="load-label">核数</div>
          </div>
        </div>
      </div>

      <!-- 内存 -->
      <div v-if="metrics.memory && metrics.memory.mem_total" class="metric-card">
        <div class="card-title">内存</div>
        <el-progress :percentage="metrics.memory.mem_used_percent" :stroke-width="14"
          :color="percentColor(metrics.memory.mem_used_percent)" />
        <div class="metric-detail">
          已用 <b>{{ formatBytes(metrics.memory.mem_used) }}</b> / 共 {{ formatBytes(metrics.memory.mem_total) }}
        </div>
      </div>

      <!-- 磁盘 -->
      <div v-if="metrics.disk && metrics.disk.disk_total" class="metric-card">
        <div class="card-title">磁盘</div>
        <el-progress :percentage="metrics.disk.disk_used_percent" :stroke-width="14"
          :color="percentColor(metrics.disk.disk_used_percent)" />
        <div class="metric-detail">
          已用 <b>{{ formatBytes(metrics.disk.disk_used) }}</b> / 共 {{ formatBytes(metrics.disk.disk_total) }}
        </div>
      </div>

      <!-- Redis -->
      <div v-if="metrics.redis" class="metric-card">
        <div class="card-title">Redis</div>
        <div class="metric-detail">已用内存 <b>{{ formatBytes(metrics.redis.redis_used_memory) }}</b></div>
      </div>

      <!-- 队列 -->
      <div class="metric-card">
        <div class="card-title">队列深度</div>
        <div class="queue-grid">
          <div class="queue-item" v-for="q in queueCards" :key="q.key">
            <span class="queue-name">{{ q.label }}</span>
            <span class="queue-val" :class="{ warn: (metrics.queues?.[q.key] ?? 0) > 0 }">
              {{ metrics.queues?.[q.key] ?? 0 }}
            </span>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { get } from '@/api/request'

const metrics = ref<any>(null)
const emptyMsg = ref('暂无数据，等待监控任务采集（每 60 秒）')

const queueCards = [
  { key: 'default', label: '默认' },
  { key: 'notify', label: '通知' },
  { key: 'export', label: '导出' },
  { key: 'task', label: '任务' },
  { key: 'processing', label: '处理中' },
  { key: 'delayed', label: '延迟' },
  { key: 'dead', label: '死信' },
]

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

function loadClass(v: number) {
  const cores = metrics.value?.cpu_count || 1
  if (v > cores * 0.9) return 'load-high'
  if (v > cores * 0.5) return 'load-mid'
  return 'load-low'
}

function percentColor(p: number) {
  if (p > 90) return '#f56c6c'
  if (p > 70) return '#e6a23c'
  return '#67c23a'
}

async function load() {
  try {
    const data = await get('/admin/monitor/metrics')
    if (data?.empty) {
      metrics.value = null
      emptyMsg.value = data.msg
    } else {
      metrics.value = data
    }
  } catch {}
}

let timer: number | undefined
onMounted(() => {
  load()
  timer = window.setInterval(load, 5000)
})
onBeforeUnmount(() => { if (timer) clearInterval(timer) })
</script>

<style scoped>
.monitor {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.monitor-header {
  display: flex;
  align-items: center;
  gap: 12px;
}
.header-title { font-size: 16px; font-weight: 600; }
.header-time { font-size: 12px; color: var(--el-text-color-secondary); }
.empty-state {
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 40px;
}
.metric-card {
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 16px;
}
.card-title { font-size: 14px; font-weight: 600; margin-bottom: 12px; }
.load-row { display: flex; gap: 24px; }
.load-item { text-align: center; }
.load-num { font-size: 28px; font-weight: 700; }
.load-label { font-size: 12px; color: var(--el-text-color-secondary); }
.load-low { color: var(--el-color-success); }
.load-mid { color: var(--el-color-warning); }
.load-high { color: var(--el-color-danger); }
.metric-detail { font-size: 13px; color: var(--el-text-color-regular); margin-top: 8px; }
.queue-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: 12px;
}
.queue-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 12px;
  background: var(--el-fill-color-light);
  border-radius: 6px;
}
.queue-name { font-size: 12px; color: var(--el-text-color-secondary); }
.queue-val { font-size: 22px; font-weight: 700; margin-top: 4px; }
.queue-val.warn { color: var(--el-color-warning); }
</style>
