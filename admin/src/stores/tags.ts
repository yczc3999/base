import { defineStore } from 'pinia'
import type { RouteLocationNormalized } from 'vue-router'

interface TagView {
  path: string
  name: string
  title: string
  affix?: boolean
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

      // 不重复添加
      if (this.visitedViews.some((v) => v.path === path)) return

      this.visitedViews.push({
        path,
        name: name as string,
        title,
        affix: meta?.affix as boolean,
      })

      // keep-alive 缓存
      if (meta?.cache !== false && name) {
        this.cachedViews.push(name as string)
      }
    },

    removeView(path: string) {
      const idx = this.visitedViews.findIndex((v) => v.path === path)
      if (idx > -1) {
        const view = this.visitedViews[idx]
        this.visitedViews.splice(idx, 1)
        const cIdx = this.cachedViews.indexOf(view.name)
        if (cIdx > -1) this.cachedViews.splice(cIdx, 1)
      }
    },

    removeOthers(path: string) {
      this.visitedViews = this.visitedViews.filter((v) => v.affix || v.path === path)
      this.cachedViews = this.visitedViews.filter((v) => v.name).map((v) => v.name)
    },

    removeAll() {
      this.visitedViews = this.visitedViews.filter((v) => v.affix)
      this.cachedViews = []
    },
  },
})
