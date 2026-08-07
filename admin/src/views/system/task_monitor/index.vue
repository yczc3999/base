<template>
  <div class="task-monitor">
    <!-- 队列状态卡片 -->
    <div class="queue-cards">
      <div v-for="q in queueCards" :key="q.key" class="queue-card">
        <div class="queue-num" :class="q.color">{{ queueData?.[q.key] ?? '—' }}</div>
        <div class="queue-label">{{ q.label }}</div>
        <el-tag v-if="q.key === 'dead' && (queueData?.dead ?? 0) > 0" type="danger" size="small">需关注</el-tag>
        <el-tag v-else-if="q.key === 'processing' && (queueData?.processing ?? 0) > 0" type="warning" size="small">执行中</el-tag>
      </div>
      <div class="queue-refresh">
        <span>自动刷新</span>
        <el-switch v-model="autoRefresh" size="small" />
        <el-button :icon="Refresh" circle size="small" @click="loadAll" />
      </div>
    </div>

    <!-- 任务列表 -->
    <div class="task-section">
      <div class="section-header">
        <span class="section-title">定时任务</span>
        <span class="section-hint">上次刷新 {{ lastUpdated }} · 手动触发走队列，由 worker 执行（防并发锁）</span>
      </div>
      <el-table v-loading="loading && !tasks.length" :data="tasks" border size="default" :empty-text="'暂无任务'">
        <el-table-column prop="name" label="任务名" min-width="120" />
        <el-table-column prop="class_name" label="类名" min-width="180">
          <template #default="{ row }">
            <code class="cls-name">{{ row.class_name }}</code>
          </template>
        </el-table-column>
        <el-table-column prop="interval" label="间隔" width="90" align="center">
          <template #default="{ row }">{{ row.interval }}s</template>
        </el-table-column>
        <el-table-column label="启用" width="70" align="center">
          <template #default="{ row }">
            <el-tag :type="row.enabled ? 'success' : 'info'" size="small">
              {{ row.enabled ? '是' : '否' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="最近执行" min-width="220">
          <template #default="{ row }">
            <div v-if="row.last_run" class="last-run">
              <el-tag :type="row.last_run.status === 'ok' ? 'success' : 'danger'" size="small">
                {{ row.last_run.status === 'ok' ? '成功' : '失败' }}
              </el-tag>
              <span class="run-time">{{ formatTime(row.last_run.time) }}</span>
              <span class="run-duration">({{ row.last_run.duration }}s)</span>
              <div v-if="row.last_run.error" class="run-error" :title="row.last_run.error">
                {{ row.last_run.error }}
              </div>
            </div>
            <span v-else class="no-run">— 未执行过</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="110" align="center" fixed="right">
          <template #default="{ row }">
            <el-button
type="primary" link size="small" :icon="VideoPlay"
              :disabled="!row.enabled" @click="triggerTask(row)">
              立即执行
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup lang="ts">
defineOptions({ name: 'TaskMonitor' })
import { ref, computed, watch, onBeforeUnmount } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { ElMessage } from 'element-plus/es/components/message/index'
import { confirmDialog } from '@/utils/confirm'
import { Refresh, VideoPlay } from '@element-plus/icons-vue'
import { get, post } from '@/api/request'

const autoRefresh = ref(localStorage.getItem('task_monitor_auto_refresh') !== '0')
const lastUpdated = ref('—')

// F1 · TanStack Query 轮询：5s 拉取任务/队列；autoRefresh 关闭时暂停（refetchInterval=false）
const pollInterval = computed(() => (autoRefresh.value ? 5000 : false))

const tasksQuery = useQuery({
  queryKey: ['task_monitor', 'tasks'],
  queryFn: () => get<any[]>('/admin/task_monitor/tasks'),
  refetchInterval: pollInterval,
})
const queueQuery = useQuery({
  queryKey: ['task_monitor', 'queue'],
  queryFn: () => get<any>('/admin/task_monitor/queue'),
  refetchInterval: pollInterval,
})

const tasks = computed(() => tasksQuery.data.value ?? [])
const queueData = computed(() => queueQuery.data.value ?? null)
const loading = computed(() => tasksQuery.isFetching.value)

// 持久化自动刷新开关
watch(autoRefresh, (v) => {
  localStorage.setItem('task_monitor_auto_refresh', v ? '1' : '0')
})

const queueCards = [
  { key: 'default', label: '默认队列', color: 'c-primary' },
  { key: 'notify', label: '通知队列', color: 'c-info' },
  { key: 'export', label: '导出队列', color: 'c-warning' },
  { key: 'task', label: '任务队列', color: 'c-secondary' },
  { key: 'processing', label: '处理中', color: 'c-processing' },
  { key: 'delayed', label: '延迟中', color: 'c-delayed' },
  { key: 'dead', label: '失败任务', color: 'c-danger' },
]

function formatTime(epoch: number) {
  if (!epoch) return '—'
  const d = new Date(epoch * 1000)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

async function loadAll() {
  await Promise.all([tasksQuery.refetch(), queueQuery.refetch()])
  lastUpdated.value = new Date().toLocaleTimeString()
}

async function triggerTask(row: any) {
  await confirmDialog(
    `确认立即执行「${row.name}」？将推入队列由 worker 执行。`,
    '手动触发', { type: 'warning' },
  )
  const res = await post('/admin/task_monitor/trigger', { task: row.class_name })
  ElMessage.success(res?.msg || '已触发')
  refreshTimer = window.setTimeout(loadAll, 2000)  // 稍后刷新看执行状态
}

let refreshTimer: number | undefined
onBeforeUnmount(() => {
  if (refreshTimer) clearTimeout(refreshTimer)
})
</script>

<style scoped>
.queue-cards {
  display: flex;
  align-items: flex-end;
  gap: 12px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}
.queue-card {
  flex: 1;
  min-width: 90px;
  padding: 14px 16px;
  text-align: center;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg-card);
  position: relative;
}
.queue-num { font-size: 26px; font-weight: 700; line-height: 1.2; }
.queue-label { font-size: 12px; color: var(--text-secondary); margin-top: 4px; }
.queue-card .el-tag { position: absolute; top: 6px; right: 6px; }
.queue-refresh {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--text-secondary);
  padding-bottom: 6px;
}
.c-primary { color: var(--primary); }
.c-info { color: var(--info); }
.c-warning { color: var(--warning); }
.c-secondary { color: #64748b; }
.c-processing { color: #8b5cf6; }
.c-delayed { color: #f59e0b; }
.c-danger { color: var(--danger); }

.task-section {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 16px;
}
.section-header {
  display: flex;
  align-items: baseline;
  gap: 12px;
  margin-bottom: 12px;
}
.section-title { font-size: 15px; font-weight: 600; }
.section-hint { font-size: 12px; color: var(--text-secondary); }
.cls-name { font-family: 'SF Mono', Monaco, monospace; font-size: 12px; background: var(--border-light); padding: 1px 6px; border-radius: var(--radius-lg); }
.last-run { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.run-time { font-size: 12px; }
.run-duration { font-size: 12px; color: var(--text-secondary); }
.run-error { width: 100%; font-size: 12px; color: var(--danger); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 300px; }
.no-run { font-size: 12px; color: var(--text-placeholder); }
</style>
