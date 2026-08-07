/** 主题 store — light/dark 切换, 持久化 localStorage, 联动 Element Plus .dark, 支持跟随系统 prefers-color-scheme */
import { defineStore } from 'pinia'

export type Theme = 'light' | 'dark'
const THEME_KEY = 'base_theme'
const FOLLOW_SYSTEM_KEY = 'base_theme_follow_system'

// 模块级缓存，避免把非响应式对象塞进 state
let mql: MediaQueryList | null = null

export const useThemeStore = defineStore('theme', {
  state: () => ({
    theme: (localStorage.getItem(THEME_KEY) as Theme) || 'light',
    // 默认跟随系统；用户手动选择后置 false
    followSystem: localStorage.getItem(FOLLOW_SYSTEM_KEY) !== 'false',
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
      this.setManual(this.theme === 'dark' ? 'light' : 'dark')
    },
    setManual(theme: Theme) {
      // 用户手动切换后停止跟随系统
      this.followSystem = false
      localStorage.setItem(FOLLOW_SYSTEM_KEY, 'false')
      this.apply(theme)
    },
    init() {
      // 初始主题：未手动覆盖时跟随系统 prefers-color-scheme
      const systemDark = window.matchMedia('(prefers-color-scheme: dark)').matches
      if (this.followSystem) this.apply(systemDark ? 'dark' : 'light')
      else this.apply(this.theme)

      // 监听系统偏好变化，仅在用户未手动覆盖时生效
      mql = window.matchMedia('(prefers-color-scheme: dark)')
      mql.addEventListener('change', (e) => {
        if (this.followSystem) this.apply(e.matches ? 'dark' : 'light')
      })
    },
  },
})
