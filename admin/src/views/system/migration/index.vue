<template>
  <div>
    <div class="migration-toolbar">
      <el-button type="primary" :icon="Refresh" @click="loadList">刷新</el-button>
      <el-button type="warning" :icon="VideoPlay" :loading="running" :disabled="pendingCount === 0"
        @click="runPending">
        执行全部待执行 ({{ pendingCount }})
      </el-button>
      <span class="tip">CLI 等价命令：<code>python -m app.migrate</code></span>
    </div>

    <el-table :data="list" v-loading="loading" border>
      <el-table-column prop="version" label="迁移文件" min-width="260">
        <template #default="{ row }">
          <code class="ver-name">{{ row.version }}</code>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="100" align="center">
        <template #default="{ row }">
          <el-tag :type="row.applied ? 'success' : 'info'" size="small">
            {{ row.applied ? '已执行' : '待执行' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="applied_at" label="执行时间" min-width="180">
        <template #default="{ row }">{{ row.applied ? formatTime(row.applied_at) : '—' }}</template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, VideoPlay } from '@element-plus/icons-vue'
import { get, post } from '@/api/request'

const list = ref<any[]>([])
const loading = ref(false)
const running = ref(false)

const pendingCount = computed(() => list.value.filter((m) => !m.applied).length)

function formatTime(ts: string) {
  if (!ts) return '—'
  return ts.replace('T', ' ').substring(0, 19)
}

async function loadList() {
  loading.value = true
  try {
    list.value = await get('/admin/migration/list') || []
  } catch {} finally { loading.value = false }
}

async function runPending() {
  await ElMessageBox.confirm(
    `确认执行全部 ${pendingCount.value} 个待执行迁移？迁移是幂等的（已执行会跳过），但会修改数据库结构。`,
    '执行迁移', { type: 'warning' },
  )
  running.value = true
  try {
    const res = await post('/admin/migration/run')
    ElMessage.success(`已执行 ${res?.applied ?? 0} 个迁移`)
    await loadList()
  } catch {} finally { running.value = false }
}

onMounted(loadList)
</script>

<style scoped>
.migration-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}
.tip { font-size: 12px; color: var(--text-secondary); }
.tip code {
  background: var(--border-light);
  padding: 1px 6px; border-radius: var(--radius-lg); font-size: 11px;
}
.ver-name {
  font-family: 'SF Mono', Monaco, monospace; font-size: 12px;
}
</style>
