import { defineStore } from 'pinia'
import settingApi from '@/api/modules/setting'

export const useSiteStore = defineStore('site', {
  state: () => ({
    name: 'Base Platform',
    logo: '',
    favicon: '',
    description: '',
    copyright: '© 2026 Base Platform',
    loaded: false,
  }),

  actions: {
    async load() {
      if (this.loaded) return
      try {
        const site = await settingApi.getSite() || {}
        if (site.name) this.name = site.name
        if (site.logo) this.logo = site.logo
        if (site.favicon) this.favicon = site.favicon
        if (site.description) this.description = site.description
        if (site.copyright) this.copyright = site.copyright

        // 动态更新 favicon
        if (site.favicon) {
          const link = document.querySelector('link[rel="icon"]') as HTMLLinkElement
          if (link) link.href = site.favicon
        }

        this.loaded = true
      } catch {}
    },
  },
})
