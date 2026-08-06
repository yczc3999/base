import { defineStore } from 'pinia'
import authApi from '@/api/modules/auth'
import { setToken, setRefreshToken, clearTokens, getToken } from '@/utils/auth'

interface UserInfo {
  id: number
  username: string
  nickname: string
  avatar: string
  email: string
  is_super_admin: boolean
  [key: string]: any
}

export const useUserStore = defineStore('user', {
  state: () => ({
    token: getToken(),
    userInfo: null as UserInfo | null,
    permissions: [] as string[],
    roles: [] as string[],
  }),

  getters: {
    isLoggedIn: (state) => !!state.token,
    isSuperAdmin: (state) => state.userInfo?.is_super_admin ?? false,
  },

  actions: {
    async login(username: string, password: string, captcha: Record<string, string> = {}) {
      const data = await authApi.login({ username, password, ...captcha })
      this.token = data.access_token
      setToken(data.access_token)
      setRefreshToken(data.refresh_token)
      this.userInfo = data.user
      return data
    },

    async getUserInfo() {
      const data = await authApi.info()
      this.userInfo = data
      return data
    },

    async getMenusAndPerms() {
      const data = await authApi.menus()
      this.permissions = data.permissions || []
      return data
    },

    hasPerms(perms: string | string[]): boolean {
      if (this.isSuperAdmin) return true
      if (!this.permissions.length) return true  // 权限未加载时暂时放行
      const arr = Array.isArray(perms) ? perms : [perms]
      return arr.some((p) => this.permissions.includes(p))
    },

    async logout() {
      try { await authApi.logout() } catch {}
      this.token = ''
      this.userInfo = null
      this.permissions = []
      this.roles = []
      clearTokens()
      // 重置权限 store，避免换账号后沿用旧菜单/路由
      const { usePermissionStore } = await import('@/stores/permission')
      usePermissionStore().resetState()
    },

    resetState() {
      this.token = ''
      this.userInfo = null
      this.permissions = []
      this.roles = []
      clearTokens()
    },
  },
})
