import type { RouteRecordRaw } from 'vue-router'
import type { MenuItem } from '@/stores/permission'
import { v2HiddenRoutes } from './v2'

const STATIC_V2_PATHS = new Set(v2HiddenRoutes.map((route) => route.path))

/** views 目录下的所有组件（Vite glob import） */
const viewModules = import.meta.glob('@/views/**/*.vue')

/**
 * 后端菜单 → Vue 路由
 *
 * template_path 映射规则：
 *   后端: "system/user/index"
 *   前端: () => import('@/views/system/user/index.vue')
 */
export function generateRoutes(menus: MenuItem[]): RouteRecordRaw[] {
  const routes: RouteRecordRaw[] = []

  function walk(items: MenuItem[]) {
    for (const item of items) {
      // type=2 是按钮权限，不生成路由
      if (item.type === 2) continue

      if (item.type === 1 && item.path && item.template_path) {
        if (STATIC_V2_PATHS.has(item.path)) continue
        // 菜单页面 → 生成路由
        const componentPath = `/src/views/${item.template_path}.vue`

        if (componentPath in viewModules) {
          const component = viewModules[componentPath]
          routes.push({
            path: item.path,
            name: item.slug,
            component,
            meta: {
              title: item.label,
              icon: item.icon,
              cache: item.is_cache !== false,
              affix: item.is_affix === true,
              visible: item.is_visible !== false,
            },
          })
        }
      }

      // 递归子菜单
      if (item.children?.length) {
        walk(item.children)
      }
    }
  }

  walk(menus)
  return routes
}
