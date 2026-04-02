import type { Router } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { usePermissionStore } from '@/stores/permission'
import { isLoggedIn } from '@/utils/auth'

const WHITE_LIST = ['/login', '/404']

export function setupGuard(router: Router) {
  router.beforeEach(async (to, from, next) => {
    // 设置页面标题
    const title = to.meta?.title as string
    document.title = title ? `${title} - Base Admin` : 'Base Admin'

    // 白名单直接放行
    if (WHITE_LIST.includes(to.path)) {
      return next()
    }

    // 未登录 → 跳登录页
    if (!isLoggedIn()) {
      return next(`/login?redirect=${to.path}`)
    }

    const userStore = useUserStore()
    const permStore = usePermissionStore()

    // 已加载路由 → 放行
    if (permStore.isLoaded) {
      return next()
    }

    // 未加载 → 拉取菜单 + 生成路由
    try {
      await userStore.getUserInfo()
      const dynamicRoutes = await permStore.loadMenus()
      await userStore.getMenusAndPerms()

      // 动态添加路由
      for (const route of dynamicRoutes) {
        router.addRoute('Layout', route)
      }

      // 重定向到目标页面（使用 replace 避免留下历史记录）
      return next({ ...to, replace: true })
    } catch {
      // 获取失败（token 可能过期）
      userStore.resetState()
      permStore.resetState()
      return next(`/login?redirect=${to.path}`)
    }
  })
}
