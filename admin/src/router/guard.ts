import type { Router } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { usePermissionStore } from '@/stores/permission'
import { isLoggedIn, clearTokens } from '@/utils/auth'
import { useSiteStore } from '@/stores/site'
import NProgress from '@/utils/nprogress'

const WHITE_LIST = ['/login', '/404']
let loadRetryCount = 0
const MAX_RETRY = 3

export function setupGuard(router: Router) {
  router.beforeEach(async (to, _from, next) => {
    NProgress.start()
    const siteStore = useSiteStore()
    if (!siteStore.loaded) await siteStore.load()
    const appName = siteStore.name || 'Admin'
    const title = to.meta?.title as string
    document.title = title ? `${title} - ${appName}` : appName

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
      // 用 fullPath 强制重新匹配新增的动态路由；
      // 传 { ...to } 会带上初次失败时的空 matched，vue-router 可能落回父路由被 redirect 吃到 /dashboard
      return next({ path: to.fullPath, replace: true })
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
