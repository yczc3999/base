import { defineStore } from 'pinia'
import type { RouteRecordRaw } from 'vue-router'
import authApi from '@/api/modules/auth'
import { generateRoutes } from '@/router/dynamic'

export interface MenuItem {
  id: number
  slug: string
  label: string
  icon?: string
  path?: string
  template_path?: string
  redirect?: string
  type: number
  is_visible?: boolean
  is_cache?: boolean
  is_affix?: boolean
  children?: MenuItem[]
  [key: string]: any
}

export const usePermissionStore = defineStore('permission', {
  state: () => ({
    menus: [] as MenuItem[],
    routes: [] as RouteRecordRaw[],
    isLoaded: false,
  }),

  actions: {
    async loadMenus() {
      const data = await authApi.menus()
      this.menus = data.menus || []
      this.routes = generateRoutes(this.menus)
      this.isLoaded = true
      return this.routes
    },

    resetState() {
      this.menus = []
      this.routes = []
      this.isLoaded = false
    },
  },
})
