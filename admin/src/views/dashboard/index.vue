<template>
  <div class="dashboard">
    <h2 class="page-title">仪表盘</h2>
    <p class="page-subtitle">欢迎回来，{{ userStore.userInfo?.nickname || 'Admin' }}</p>

    <div class="stat-cards">
      <div v-for="stat in statCards" :key="stat.label" class="stat-card" :style="{ '--accent': stat.color }">
        <div class="stat-icon">{{ stat.icon }}</div>
        <div class="stat-info">
          <div class="stat-value">{{ stat.value }}</div>
          <div class="stat-label">{{ stat.label }}</div>
        </div>
      </div>
    </div>

    <div class="dashboard-row">
      <div class="content-card">
        <div class="card-header"><span class="card-title">最近操作</span></div>
        <div class="card-body" style="padding:0">
          <table class="mini-table">
            <thead><tr><th>用户</th><th>操作</th><th>方法</th><th>IP</th><th>耗时</th><th>时间</th></tr></thead>
            <tbody>
              <tr v-for="log in recentLogs" :key="log.id">
                <td><strong>{{ log.username || '—' }}</strong></td>
                <td>{{ log.module }}/{{ log.action }}</td>
                <td><span class="method-tag" :class="log.method?.toLowerCase()">{{ log.method }}</span></td>
                <td class="mono">{{ log.ip }}</td>
                <td class="mono">{{ log.duration }}ms</td>
                <td class="mono">{{ fmt(log.created_at) }}</td>
              </tr>
              <tr v-if="!recentLogs.length"><td colspan="6" style="text-align:center;color:var(--text-placeholder);padding:24px">暂无数据</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <div class="content-card">
        <div class="card-header"><span class="card-title">系统状态</span><span class="dot-live"></span></div>
        <div class="card-body">
          <div class="sys-row" v-for="item in sysRows" :key="item.label">
            <span class="sys-label">{{ item.label }}</span>
            <span class="sys-value" :class="item.cls">{{ item.value }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useUserStore } from '@/stores/user'
import { get } from '@/api/request'

const userStore = useUserStore()
const sd = ref<any>({}), sys = ref<any>({}), recentLogs = ref<any[]>([])

onMounted(async () => {
  try {
    const [s, y, l] = await Promise.all([
      get('/admin/dashboard/stats', {}, { showError: false }),
      get('/admin/dashboard/system', {}, { showError: false }),
      get('/admin/dashboard/recent', {}, { showError: false }),
    ])
    sd.value = s || {}; sys.value = y || {}; recentLogs.value = l || []
  } catch {}
})

const statCards = computed(() => [
  { label: '用户总数', value: sd.value.total_users ?? '—', icon: '👤', color: '#3B82F6' },
  { label: '今日登录', value: sd.value.today_logins ?? '—', icon: '→', color: '#16A34A' },
  { label: '本月操作', value: sd.value.month_operations ?? '—', icon: '◉', color: '#F59E0B' },
  { label: '未读消息', value: sd.value.unread_messages ?? '—', icon: '🔔', color: '#EF4444' },
])

const sysRows = computed(() => [
  { label: '系统版本', value: sys.value.version || '—' },
  { label: '运行框架', value: sys.value.framework || '—' },
  { label: '操作系统', value: sys.value.os || '—' },
  { label: 'CPU 核心', value: sys.value.cpu_count || '—' },
  { label: '数据库', value: sys.value.database || '—', cls: 'green' },
  { label: 'Redis', value: `v${sys.value.redis_version || '—'}`, cls: 'green' },
  { label: 'Redis 内存', value: sys.value.redis_memory || '—' },
  { label: 'Redis Keys', value: sys.value.redis_keys ?? '—' },
])

function fmt(v: string) { return v ? v.replace('T',' ').substring(11,19) : '—' }
</script>

<style scoped lang="scss">
.stat-cards { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin: 20px 0; }

.stat-card {
  background: var(--bg-card); border: 1px solid var(--border); border-left: 3px solid var(--accent);
  border-radius: var(--radius); padding: 18px 20px; display: flex; align-items: center; gap: 14px;
}

.stat-icon {
  width: 38px; height: 38px; background: color-mix(in srgb, var(--accent) 10%, transparent);
  display: flex; align-items: center; justify-content: center; border-radius: var(--radius); font-size: 16px;
}

.stat-value { font-size: 26px; font-weight: 700; color: var(--text-primary); line-height: 1; }
.stat-label { font-size: 12px; color: var(--text-secondary); margin-top: 4px; }

.dashboard-row { display: grid; grid-template-columns: 1fr 320px; gap: 16px; }

.mini-table {
  width: 100%; border-collapse: collapse; font-size: 12px;
  th { padding: 10px 14px; text-align: left; font-weight: 600; color: var(--text-secondary); background: #F8FAFC; border-bottom: 1px solid var(--border); font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; }
  td { padding: 9px 14px; border-bottom: 1px solid var(--border-light); }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: #FAFBFC; }
}

.mono { font-family: var(--font-mono, monospace); font-size: 11px; }

.method-tag {
  display: inline-block; padding: 1px 6px; font-size: 10px; font-weight: 600;
  font-family: var(--font-mono, monospace); border-radius: var(--radius-sm);
  &.post { background: #DBEAFE; color: #2563EB; }
  &.get { background: #DCFCE7; color: #16A34A; }
  &.delete { background: #FEE2E2; color: #EF4444; }
}

.sys-row {
  display: flex; justify-content: space-between; align-items: center;
  padding: 9px 0; border-bottom: 1px solid var(--border-light); font-size: 13px;
  &:last-child { border-bottom: none; }
}
.sys-label { color: var(--text-secondary); }
.sys-value { color: var(--text-primary); font-weight: 500; }
.green { color: var(--success) !important; font-weight: 600 !important; }

.dot-live {
  width: 6px; height: 6px; background: var(--success); border-radius: 50%;
  animation: pulse 2s ease-in-out infinite;
}
@keyframes pulse { 0%,100% { opacity:1 } 50% { opacity:0.4 } }
</style>
