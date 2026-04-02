<template>
  <div class="header">
    <div class="header-left">
      <button class="toggle-btn" @click="appStore.toggleSidebar">
        <span>☰</span>
      </button>
      <el-breadcrumb separator="/">
        <el-breadcrumb-item :to="{ path: '/dashboard' }">首页</el-breadcrumb-item>
        <el-breadcrumb-item v-for="item in breadcrumbs" :key="item.path">
          {{ item.title }}
        </el-breadcrumb-item>
      </el-breadcrumb>
    </div>
    <div class="header-right">
      <!-- 消息铃铛 -->
      <button class="header-action" @click="$router.push('/message')">
        <span>🔔</span>
        <span v-if="unreadCount > 0" class="badge">{{ unreadCount > 99 ? '99+' : unreadCount }}</span>
      </button>

      <!-- 用户菜单 -->
      <el-dropdown trigger="click" @command="handleCommand">
        <div class="user-info">
          <div class="user-avatar">{{ userStore.userInfo?.nickname?.charAt(0) || 'A' }}</div>
          <span class="user-name">{{ userStore.userInfo?.nickname || 'Admin' }}</span>
          <span class="user-arrow">▾</span>
        </div>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="profile">个人中心</el-dropdown-item>
            <el-dropdown-item divided command="logout">退出登录</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useAppStore } from '@/stores/app'
import { useUserStore } from '@/stores/user'
import { useRoute, useRouter } from 'vue-router'
import messageApi from '@/api/modules/message'

const appStore = useAppStore()
const userStore = useUserStore()
const route = useRoute()
const router = useRouter()

const unreadCount = ref(0)

const breadcrumbs = computed(() => {
  const matched = route.matched.filter((r) => r.meta?.title)
  return matched.map((r) => ({ path: r.path, title: r.meta.title as string }))
})

async function fetchUnread() {
  try {
    const data = await messageApi.unreadCount()
    unreadCount.value = data?.count || 0
  } catch {}
}

function handleCommand(cmd: string) {
  if (cmd === 'profile') router.push('/profile')
  if (cmd === 'logout') {
    userStore.logout().then(() => router.push('/login'))
  }
}

onMounted(() => { fetchUnread() })
// 每 60 秒刷新未读数
const timer = setInterval(fetchUnread, 60000)
onUnmounted(() => clearInterval(timer))
</script>

<style scoped lang="scss">
.header {
  height: var(--header-height);
  background: var(--bg-header);
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 var(--space-lg);
  flex-shrink: 0;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 16px;
}

.toggle-btn {
  width: 32px; height: 32px;
  display: flex; align-items: center; justify-content: center;
  background: none; border: 1px solid var(--border);
  border-radius: var(--radius);
  font-size: 16px;
  color: var(--text-secondary);
  transition: all var(--transition-fast);

  &:hover { background: var(--bg-page); color: var(--text-primary); }
}

.header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.header-action {
  position: relative;
  width: 36px; height: 36px;
  display: flex; align-items: center; justify-content: center;
  background: none; border: none;
  border-radius: var(--radius);
  font-size: 18px;
  transition: background var(--transition-fast);

  &:hover { background: var(--bg-page); }

  .badge {
    position: absolute;
    top: 2px; right: 2px;
    min-width: 16px; height: 16px;
    background: var(--danger);
    color: white;
    font-size: 10px; font-weight: 600;
    display: flex; align-items: center; justify-content: center;
    border-radius: 8px;
    padding: 0 4px;
  }
}

.user-info {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 8px;
  border-radius: var(--radius);
  cursor: pointer;
  transition: background var(--transition-fast);

  &:hover { background: var(--bg-page); }
}

.user-avatar {
  width: 28px; height: 28px;
  background: var(--primary);
  color: white;
  border-radius: var(--radius);
  display: flex; align-items: center; justify-content: center;
  font-size: 12px; font-weight: 600;
}

.user-name {
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--text-primary);
}

.user-arrow {
  font-size: 10px;
  color: var(--text-secondary);
}
</style>
