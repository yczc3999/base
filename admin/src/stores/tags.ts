import { defineStore } from 'pinia'
import type { RouteLocationNormalized } from 'vue-router'

interface TagView {
  path: string
  name: string
  title: string
  affix?: boolean
  /** 菜单 icon slug（来自 route.meta.icon），TagsView 用它渲染 lucide 图标 */
  icon?: string
}

/** slug (kebab-case) → PascalCase：admin-user → AdminUser */
function toPascal(slug: string): string {
  return slug.split(/[-_]/).map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join('')
}

export const useTagsStore = defineStore('tags', {
  state: () => ({
    visitedViews: [] as TagView[],
    cachedViews: [] as string[],
  }),

  actions: {
    addView(route: RouteLocationNormalized) {
      const { path, name, meta } = route
      const title = (meta?.title as string) || (name as string) || path

      // 不重复添加（已存在则仅刷新 icon，避免首次带 icon、后续更新丢失）
      const existing = this.visitedViews.find((v) => v.path === path)
      if (existing) {
        if (!existing.icon && meta?.icon) existing.icon = meta.icon as string
        return
      }

      this.visitedViews.push({
        path,
        name: name as string,
        title,
        affix: meta?.affix as boolean,
        icon: (meta?.icon as string) || undefined,
      })

      // keep-alive 缓存：组件 name 与路由 name(slug) 的 PascalCase 形式对应
      if (meta?.cache !== false && name) {
        this.cachedViews.push(toPascal(name as string))
      }
    },

    removeView(path: string) {
      const idx = this.visitedViews.findIndex((v) => v.path === path)
      if (idx > -1) {
        const view = this.visitedViews[idx]
        this.visitedViews.splice(idx, 1)
        const cIdx = this.cachedViews.indexOf(toPascal(view.name))
        if (cIdx > -1) this.cachedViews.splice(cIdx, 1)
      }
    },

    removeOthers(path: string) {
      this.visitedViews = this.visitedViews.filter((v) => v.affix || v.path === path)
      this.cachedViews = this.visitedViews.filter((v) => v.name).map((v) => toPascal(v.name))
    },

    removeAll() {
      this.visitedViews = this.visitedViews.filter((v) => v.affix)
      this.cachedViews = []
    },
  },
})
