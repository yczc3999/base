<template>
  <PageShell
    :title="$t('dashboard.title')"
    :sub-title="$t('dashboard.welcome', { name: userStore.userInfo?.nickname || 'Admin' })"
  >
  <div class="dashboard">
    <div class="stat-cards">
      <StatCard
        v-for="stat in statCards"
        :key="stat.label"
        :icon="stat.icon"
        :value="stat.value"
        :label="stat.label"
        :accent="stat.color"
        :count="stat.count"
      />
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
              <tr v-if="!recentLogs.length"><td colspan="6" style="text-align:center;padding:32px"><EmptyState :icon="Activity" title="暂无操作记录" description="进行增删改操作后会显示在这里" /></td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <div class="content-card">
        <div class="card-header">
          <span class="card-title">系统状态</span>
          <span class="header-live">
            <span class="dot-live" role="img" aria-label="系统状态运行中"></span>
            <span class="live-text">运行中</span>
          </span>
        </div>
        <div class="card-body">
          <div v-for="item in sysRows" :key="item.label" class="sys-row">
            <span class="sys-label">
              <span v-if="item.status" class="sys-dot" :class="item.status === '正常' ? 'ok' : 'err'"></span>
              {{ item.label }}
            </span>
            <span class="sys-value-wrap">
              <span class="sys-value" :class="item.cls">{{ item.value }}</span>
              <span v-if="item.status" class="sys-status" :class="item.status === '正常' ? 'ok' : 'err'">{{ item.status }}</span>
            </span>
          </div>
        </div>
      </div>
    </div>
  </div>
  </PageShell>
</template>

<script setup lang="ts">
defineOptions({ name: 'Dashboard' })
import { computed } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { useUserStore } from '@/stores/user'
import { get } from '@/api/request'
import PageShell from '@/components/PageShell/index.vue'
import StatCard from '@/components/StatCard/index.vue'
import EmptyState from '@/components/EmptyState/index.vue'
import { Users, LogIn, Activity, Bell } from 'lucide-vue-next'

const userStore = useUserStore()

// F1 · 3 个独立 query 并行拉取（TanStack Query 统一缓存/状态管理）
const statsQuery = useQuery({
  queryKey: ['dashboard', 'stats'],
  queryFn: () => get<any>('/admin/dashboard/stats', {}, { showError: false }),
})
const systemQuery = useQuery({
  queryKey: ['dashboard', 'system'],
  queryFn: () => get<any>('/admin/dashboard/system', {}, { showError: false }),
})
const recentQuery = useQuery({
  queryKey: ['dashboard', 'recent'],
  queryFn: () => get<any[]>('/admin/dashboard/recent', {}, { showError: false }),
})

const sd = computed(() => statsQuery.data.value ?? {})
const sys = computed(() => systemQuery.data.value ?? {})
const recentLogs = computed(() => recentQuery.data.value ?? [])

const statCards = computed(() => [
  { label: '用户总数', value: sd.value.total_users ?? '—', icon: Users, color: 'var(--primary)', count: true },
  { label: '今日登录', value: sd.value.today_logins ?? '—', icon: LogIn, color: 'var(--success)', count: true },
  { label: '本月操作', value: sd.value.month_operations ?? '—', icon: Activity, color: 'var(--warning)', count: true },
  { label: '未读消息', value: sd.value.unread_messages ?? '—', icon: Bell, color: 'var(--danger)', count: true },
])

function healthy(v: any) { return v !== undefined && v !== null && v !== '' }

const sysRows = computed(() => [
  { label: '系统版本', value: sys.value.version || '—' },
  { label: '运行框架', value: sys.value.framework || '—' },
  { label: '操作系统', value: sys.value.os || '—' },
  { label: 'CPU 核心', value: sys.value.cpu_count || '—' },
  { label: '数据库', value: sys.value.database || '—', cls: 'green', status: healthy(sys.value.database) ? '正常' : '异常' },
  { label: 'Redis', value: `v${sys.value.redis_version || '—'}`, cls: 'green', status: healthy(sys.value.redis_version) ? '正常' : '异常' },
  { label: 'Redis 内存', value: sys.value.redis_memory || '—' },
  { label: 'Redis Keys', value: sys.value.redis_keys ?? '—' },
])

function fmt(v: string) { return v ? v.replace('T',' ').substring(11,19) : '—' }
</script>

<style scoped lang="scss">
.stat-cards { display: grid; grid-template-columns: repeat(4, 1fr); gap: var(--space-base); margin: var(--space-lg) 0; }

.dashboard-row { display: grid; grid-template-columns: 1fr 320px; gap: var(--space-base); }

.mini-table {
  width: 100%; border-collapse: collapse; font-size: var(--text-xs);
  th { padding: var(--space-sm) var(--space-md); text-align: left; font-weight: 600; color: var(--text-secondary); background: var(--bg-table-header); border-bottom: 1px solid var(--border); font-size: var(--text-xs); text-transform: uppercase; letter-spacing: 0.04em; }
  td { padding: var(--space-sm) var(--space-md); border-bottom: 1px solid var(--border-light); }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: var(--bg-subtle); }
}

.mono { font-family: var(--font-mono, monospace); font-size: var(--text-xs); }

.method-tag {
  display: inline-block; padding: 1px var(--space-xs); font-size: var(--text-xs); font-weight: 600;
  font-family: var(--font-mono, monospace); border-radius: var(--radius-sm);
  &.post { background: var(--primary-bg); color: var(--primary-hover); }
  &.get { background: var(--success-bg); color: var(--success); }
  &.delete { background: var(--danger-bg); color: var(--danger); }
}

.sys-row {
  display: flex; justify-content: space-between; align-items: center;
  padding: var(--space-sm) 0; border-bottom: 1px solid var(--border-light); font-size: var(--text-sm);
  &:last-child { border-bottom: none; }
}
.sys-label { color: var(--text-secondary); display: flex; align-items: center; gap: var(--space-xs); }

.sys-dot {
  width: 6px; height: 6px; border-radius: 50%;
  flex-shrink: 0;
  &.ok { background: var(--success); }
  &.err { background: var(--danger); }
}
.sys-value { color: var(--text-primary); font-weight: 500; }
.green { color: var(--success) !important; font-weight: 600 !important; }

.header-live { display: flex; align-items: center; gap: var(--space-xs); }
.live-text { font-size: var(--text-xs); color: var(--success); font-weight: 500; }

.sys-value-wrap { display: flex; align-items: center; gap: var(--space-sm); }
.sys-status { font-size: var(--text-xs); font-weight: 600; }
.sys-status.ok { color: var(--success); }
.sys-status.err { color: var(--danger); }

.dot-live {
  width: 6px; height: 6px; background: var(--success); border-radius: 50%;
  animation: pulse 2s ease-in-out infinite;
}
@keyframes pulse { 0%,100% { opacity:1 } 50% { opacity:0.4 } }
</style>
