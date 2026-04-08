import type { Router } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { usePermissionStore } from '@/stores/permission'
import { isLoggedIn, clearTokens } from '@/utils/auth'
import NProgress from '@/utils/nprogress'

const WHITE_LIST = ['/login', '/404']
let loadRetryCount = 0
const MAX_RETRY = 3

export function setupGuard(router: Router) {
  router.beforeEach(async (to, _from, next) => {
    NProgress.start()
    const title = to.meta?.title as string
    const appTitle = import.meta.env.VITE_APP_TITLE || 'Base Admin'
    document.title = title ? `${title} - ${appTitle}` : appTitle

    if (WHITE_LIST.includes(to.path)) {
      loadRetryCount = 0
      return next()
    }

    if (!isLoggedIn()) {
      return next(`/login?redirect=${to.path}`)
    }

    const userStore = useUserStore()
    const permStore = usePermissionStore()

    if (permStore.isLoaded) {
      return next()
    }

    // 防止无限重试（后端挂了时）
    if (loadRetryCount >= MAX_RETRY) {
      loadRetryCount = 0
      userStore.resetState()
      permStore.resetState()
      clearTokens()
      return next('/login')
    }

    try {
      loadRetryCount++
      await userStore.getUserInfo()
      const dynamicRoutes = await permStore.loadMenus()  // 内部已同步 permissions 到 userStore

      for (const route of dynamicRoutes) {
        router.addRoute('Layout', route)
      }

      loadRetryCount = 0
      return next({ ...to, replace: true })
    } catch {
      userStore.resetState()
      permStore.resetState()
      clearTokens()
      loadRetryCount = 0
      return next(`/login?redirect=${to.path}`)
    }
  })

  router.afterEach(() => {
    NProgress.done()
  })
}
