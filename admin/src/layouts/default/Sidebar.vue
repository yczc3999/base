<template>
  <div class="sidebar" :class="{ collapsed: appStore.sidebarCollapsed }">
    <!-- Logo -->
    <div class="sidebar-logo">
      <img v-if="siteStore.logo" :src="siteStore.logo" class="logo-img" />
      <div v-else class="logo-icon">{{ siteStore.name?.charAt(0) || 'B' }}</div>
      <span v-show="!appStore.sidebarCollapsed" class="logo-text">
        <strong>{{ siteStore.name?.split(' ')[0] || 'Base' }}</strong>
        <span class="logo-sub">{{ siteStore.name?.split(' ').slice(1).join(' ') || 'Platform' }}</span>
      </span>
    </div>

    <!-- Menu -->
    <nav class="sidebar-menu">
      <el-scrollbar>
        <el-menu
          :default-active="activeMenu"
          :collapse="appStore.sidebarCollapsed"
          :collapse-transition="false"
          background-color="transparent"
          text-color="#94A3B8"
          active-text-color="#FFFFFF"
          :unique-opened="true"
          router
        >
          <template v-for="menu in permStore.menus" :key="menu.slug">
            <!-- 有子菜单 -->
            <el-sub-menu v-if="menu.children?.length" :index="menu.slug">
              <template #title>
                <span class="menu-icon">{{ getMenuIcon(menu.icon) }}</span>
                <span>{{ menu.label }}</span>
              </template>
              <template v-for="child in menu.children" :key="child.slug">
                <!-- 子目录（如日志管理）→ 展开子菜单 -->
                <el-sub-menu v-if="child.type === 0 && child.children?.length" :index="child.slug">
                  <template #title>
                    <span class="menu-icon">{{ getMenuIcon(child.icon) }}</span>
                    <span>{{ child.label }}</span>
                  </template>
                  <el-menu-item
                    v-for="sub in child.children"
                    :key="sub.slug"
                    v-show="sub.type !== 2 && sub.is_visible !== false"
                    :index="sub.path || sub.slug"
                  >
                    <span class="menu-icon">{{ getMenuIcon(sub.icon) }}</span>
                    <span>{{ sub.label }}</span>
                  </el-menu-item>
                </el-sub-menu>
                <!-- 普通菜单项 -->
                <el-menu-item
                  v-else-if="child.type !== 2 && child.is_visible !== false"
                  :index="child.path || child.slug"
                >
                  <span class="menu-icon">{{ getMenuIcon(child.icon) }}</span>
                  <span>{{ child.label }}</span>
                </el-menu-item>
              </template>
            </el-sub-menu>
            <!-- 无子菜单 -->
            <el-menu-item
              v-else-if="menu.is_visible !== false && menu.type !== 2"
              :index="menu.path || menu.slug"
            >
              <span class="menu-icon">{{ getMenuIcon(menu.icon) }}</span>
              <span>{{ menu.label }}</span>
            </el-menu-item>
          </template>
        </el-menu>
      </el-scrollbar>
    </nav>
  </div>
</template>

<script setup lang="ts">
import { useAppStore } from '@/stores/app'
import { usePermissionStore } from '@/stores/permission'
import { useSiteStore } from '@/stores/site'
import { useRoute } from 'vue-router'

const appStore = useAppStore()
const permStore = usePermissionStore()
const siteStore = useSiteStore()
const route = useRoute()

onMounted(() => { siteStore.load() })

const activeMenu = computed(() => route.path)

const iconMap: Record<string, string> = {
  // 系统
  'Settings': '⚙', 'Users': '👤', 'Shield': '🛡', 'Menu': '☰',
  'Sliders': '⊞', 'FileText': '📄', 'Activity': '◉', 'LogIn': '→',
  'LayoutDashboard': '▦', 'UserCircle': '◎', 'Bell': '🔔',
  // 配置
  'Globe': '🌐', 'MessageSquare': '💬', 'HardDrive': '💾',
  'CreditCard': '💳', 'Mail': '✉',
  // 通用
  'Home': '⌂', 'Search': '🔍', 'Star': '★', 'Heart': '♥',
  'Lock': '🔒', 'Key': '🔑', 'Database': '🗄', 'Server': '🖥',
  'Cloud': '☁', 'Package': '📦', 'ShoppingCart': '🛒', 'Store': '🏪',
  'BarChart3': '📊', 'PieChart': '◔', 'Tag': '🏷', 'Folder': '📁',
  'Image': '🖼', 'Camera': '📷', 'Phone': '📞', 'Cpu': '⚡',
}

function getMenuIcon(icon?: string): string {
  if (!icon) return '·'
  return iconMap[icon] || '·'
}
</script>

<style scoped lang="scss">
.sidebar {
  width: var(--sidebar-width);
  height: 100vh;
  background: var(--bg-sidebar);
  display: flex;
  flex-direction: column;
  transition: width var(--transition-slow);
  overflow: hidden;
  flex-shrink: 0;

  &.collapsed { width: var(--sidebar-collapsed); }
}

.sidebar-logo {
  height: var(--header-height);
  display: flex;
  align-items: center;
  padding: 0 16px;
  gap: 12px;
  border-bottom: 1px solid rgba(255,255,255,0.06);
}

.logo-img {
  width: 32px; height: 32px;
  object-fit: contain;
  border-radius: var(--radius);
  flex-shrink: 0;
}

.logo-icon {
  width: 32px; height: 32px;
  background: var(--primary);
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  font-size: 16px;
  border-radius: var(--radius);
  flex-shrink: 0;
}

.logo-text {
  color: #F1F5F9;
  font-size: 16px;
  white-space: nowrap;
  .logo-sub { color: var(--primary-light); font-weight: 400; }
}

.sidebar-menu {
  flex: 1;
  overflow: hidden;
  padding-top: 8px;
}

.menu-icon {
  margin-right: 8px;
  font-size: 14px;
  width: 20px;
  display: inline-block;
  text-align: center;
}

// Element Plus menu 覆写 — 扁平直角风格
:deep(.el-menu) {
  border-right: none !important;

  .el-menu-item, .el-sub-menu__title {
    height: 44px;
    line-height: 44px;
    margin: 0 8px;
    border-radius: var(--radius);
    font-size: var(--text-sm);

    &:hover { background: rgba(255,255,255,0.05) !important; }
  }

  .el-menu-item.is-active {
    background: rgba(37,99,235,0.12) !important;
    color: #FFFFFF !important;
    position: relative;

    &::before {
      content: '';
      position: absolute;
      left: 0; top: 8px; bottom: 8px;
      width: 3px;
      background: var(--primary);
      border-radius: 0 2px 2px 0;
    }
  }
}
</style>
