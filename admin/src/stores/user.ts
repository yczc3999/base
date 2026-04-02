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
    async login(username: string, password: string) {
      const data = await authApi.login({ username, password })
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
      // 超管 或 权限列表未加载时放行（等加载完再校验）
      if (this.isSuperAdmin || !this.permissions.length) return true
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
