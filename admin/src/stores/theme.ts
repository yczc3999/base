/** 主题 store — light/dark 切换, 持久化 localStorage, 联动 Element Plus .dark */
import { defineStore } from 'pinia'

export type Theme = 'light' | 'dark'
const THEME_KEY = 'base_theme'

export const useThemeStore = defineStore('theme', {
  state: () => ({
    theme: (localStorage.getItem(THEME_KEY) as Theme) || 'light',
  }),

  actions: {
    apply(theme: Theme) {
      this.theme = theme
      localStorage.setItem(THEME_KEY, theme)
      const el = document.documentElement
      if (theme === 'dark') {
        el.setAttribute('data-theme', 'dark')
        el.classList.add('dark')        // Element Plus 暗色变量
      } else {
        el.removeAttribute('data-theme')
        el.classList.remove('dark')
      }
    },
    toggle() {
      this.apply(this.theme === 'dark' ? 'light' : 'dark')
    },
    init() {
      this.apply(this.theme)
    },
  },
})
