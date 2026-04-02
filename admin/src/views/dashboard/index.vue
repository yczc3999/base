<template>
  <div class="dashboard">
    <h2 class="page-title">仪表盘</h2>
    <p style="color: var(--text-secondary); margin-top: 8px;">欢迎回来，{{ userStore.userInfo?.nickname || 'Admin' }}</p>

    <!-- Stat Cards -->
    <div class="stat-cards">
      <div v-for="stat in stats" :key="stat.label" class="stat-card" :style="{ '--accent': stat.color }">
        <div class="stat-icon">{{ stat.icon }}</div>
        <div class="stat-info">
          <div class="stat-value">{{ stat.value }}</div>
          <div class="stat-label">{{ stat.label }}</div>
        </div>
      </div>
    </div>

    <div class="dashboard-grid">
      <div class="card">
        <div class="card-header">系统信息</div>
        <div class="card-body">
          <div class="info-row" v-for="item in sysInfo" :key="item.label">
            <span class="info-label">{{ item.label }}</span>
            <span class="info-value">{{ item.value }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useUserStore } from '@/stores/user'

const userStore = useUserStore()

const stats = [
  { label: '用户总数', value: '—', icon: '👤', color: '#2563EB' },
  { label: '今日登录', value: '—', icon: '→', color: '#16A34A' },
  { label: '本月操作', value: '—', icon: '◉', color: '#F59E0B' },
  { label: '未读消息', value: '—', icon: '🔔', color: '#EF4444' },
]

const sysInfo = [
  { label: '系统版本', value: 'v1.0.0' },
  { label: '运行框架', value: 'FastAPI + Vue 3' },
  { label: '数据库', value: 'PostgreSQL 16' },
  { label: '缓存', value: 'Redis' },
]
</script>

<style scoped lang="scss">
.page-title {
  font-size: var(--text-xl);
  font-weight: 600;
  color: var(--text-primary);
}

.stat-cards {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-top: 20px;
}

.stat-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-left: 4px solid var(--accent);
  border-radius: var(--radius);
  padding: 20px;
  display: flex;
  align-items: center;
  gap: 16px;
}

.stat-icon {
  width: 40px; height: 40px;
  background: color-mix(in srgb, var(--accent) 10%, transparent);
  display: flex; align-items: center; justify-content: center;
  border-radius: var(--radius);
  font-size: 18px;
}

.stat-value {
  font-size: var(--text-2xl);
  font-weight: 700;
  color: var(--text-primary);
}

.stat-label {
  font-size: var(--text-xs);
  color: var(--text-secondary);
  margin-top: 2px;
}

.dashboard-grid {
  margin-top: 20px;
}

.card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
}

.card-header {
  padding: 16px 20px;
  font-weight: 600;
  font-size: var(--text-base);
  border-bottom: 1px solid var(--border);
}

.card-body { padding: 20px; }

.info-row {
  display: flex;
  justify-content: space-between;
  padding: 10px 0;
  border-bottom: 1px solid var(--border-light);
  font-size: var(--text-sm);

  &:last-child { border-bottom: none; }

  .info-label { color: var(--text-secondary); }
  .info-value { color: var(--text-primary); font-weight: 500; }
}
</style>
