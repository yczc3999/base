<template>
  <div>
    <div class="session-header">
      <span class="count">在线会话 <b>{{ sessions.length }}</b> 个</span>
      <el-button :icon="Refresh" size="small" circle @click="load" />
    </div>

    <el-table v-loading="loading" :data="sessions" stripe :empty-text="'暂无在线会话'">
      <el-table-column prop="username" label="用户名" min-width="120" />
      <el-table-column label="端" width="80" align="center">
        <template #default="{ row }">
          <el-tag :type="row.scope === 'admin' ? 'warning' : 'primary'" size="small">
            {{ row.scope === 'admin' ? '后台' : '前台' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="user_id" label="用户ID" width="80" align="center" />
      <el-table-column prop="token" label="Token" min-width="160">
        <template #default="{ row }">
          <code class="tok">{{ row.token }}</code>
        </template>
      </el-table-column>
      <el-table-column label="剩余有效期" width="130" align="center">
        <template #default="{ row }">
          <el-tag :type="row.ttl < 300 ? 'danger' : 'success'" size="small" effect="plain">
            {{ formatTtl(row.ttl) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="过期时间" min-width="170">
        <template #default="{ row }">{{ formatTime(row.expires_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="90" align="center" fixed="right">
        <template #default="{ row }">
          <el-button type="danger" link size="small" :icon="SwitchButton" @click="kick(row)">
            踢下线
          </el-button>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup lang="ts">
defineOptions({ name: 'Session' })
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index'
import { confirmDialog } from '@/utils/confirm'
import { Refresh, SwitchButton } from '@element-plus/icons-vue'
import { get, post } from '@/api/request'

const sessions = ref<any[]>([])
const loading = ref(false)

function formatTtl(ttl: number) {
  if (ttl <= 0) return '已过期'
  const h = Math.floor(ttl / 3600)
  const m = Math.floor((ttl % 3600) / 60)
  if (h > 0) return `${h} 小时 ${m} 分`
  return `${m} 分钟`
}

function formatTime(epoch: number) {
  return new Date(epoch * 1000).toLocaleString()
}

async function load() {
  loading.value = true
  try {
    sessions.value = await get('/admin/session/list') || []
  } catch { /* 会话列表加载失败静默 */ } finally { loading.value = false }
}

async function kick(row: any) {
  await confirmDialog(
    `确认将「${row.username}」踢下线？该用户所有设备将立即退出登录。`,
    '踢下线', { type: 'warning' },
  )
  await post('/admin/session/kick', { scope: row.scope, user_id: row.user_id })
  ElMessage.success('已踢下线')
  load()
}

onMounted(load)
</script>

<style scoped>
.session-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}
.count { font-size: 14px; }
.tok { font-family: 'SF Mono', Monaco, monospace; font-size: 12px; }
</style>
