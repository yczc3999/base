<template>
  <header class="header">
    <div class="header-left">
      <button class="toggle-btn" :aria-label="appStore.sidebarCollapsed ? '展开菜单' : '折叠菜单'" @click="appStore.toggleSidebar">
        <PanelLeftClose v-if="appStore.sidebarCollapsed" :size="18" />
        <PanelLeftOpen v-else :size="18" />
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

      <!-- 主题切换 (P2-4) -->
      <button class="header-action" :title="themeStore.theme === 'dark' ? '切换亮色' : '切换暗色'" @click="themeStore.toggle()">
        <span>{{ themeStore.theme === 'dark' ? '🌙' : '☀️' }}</span>
      </button>

      <!-- 语言切换 (P2-3) -->
      <el-dropdown trigger="click" @command="switchLocale">
        <button class="header-action" :title="$t('common.language')">
          <span>{{ locale === 'en-US' ? 'EN' : '中' }}</span>
        </button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="zh-CN" :class="{ active: locale === 'zh-CN' }">简体中文</el-dropdown-item>
            <el-dropdown-item command="en-US" :class="{ active: locale === 'en-US' }">English</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>

      <!-- 用户菜单 -->
      <el-dropdown trigger="click" @command="handleCommand">
        <div class="user-info">
          <div class="user-avatar">{{ userStore.userInfo?.nickname?.charAt(0) || 'A' }}</div>
          <span class="user-name">{{ userStore.userInfo?.nickname || 'Admin' }}</span>
          <span class="user-arrow">▾</span>
        </div>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="profile">{{ $t('layout.profile') }}</el-dropdown-item>
            <el-dropdown-item divided command="logout">{{ $t('layout.logout') }}</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>
  </header>
</template>

<script setup lang="ts">
import { useAppStore } from '@/stores/app'
import { useUserStore } from '@/stores/user'
import { useThemeStore } from '@/stores/theme'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { PanelLeftClose, PanelLeftOpen } from 'lucide-vue-next'
import { saveLocale } from '@/locales'
import messageApi from '@/api/modules/message'

const appStore = useAppStore()
const userStore = useUserStore()
const themeStore = useThemeStore()
const route = useRoute()
const router = useRouter()
const { locale } = useI18n()

function switchLocale(lang: string) {
  if (lang === 'zh-CN' || lang === 'en-US') {
    locale.value = lang
    saveLocale(lang)
  }
}

const unreadCount = ref(0)

const breadcrumbs = computed(() => {
  const matched = route.matched.filter((r) => r.meta?.title)
  return matched.map((r) => ({ path: r.path, title: r.meta.title as string }))
})

async function fetchUnread() {
  try {
    const data = await messageApi.unreadCount()
    unreadCount.value = data?.count || 0
  } catch { /* 未读数获取失败保持为 0 */ }
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
