import type { RouteRecordRaw } from 'vue-router'
import { isLoggedIn } from '@/utils/auth'
import { v2HiddenRoutes } from './v2'

/** 静态路由（不需要登录） */
export const staticRoutes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/login/index.vue'),
    meta: { title: '登录', layout: 'blank' },
  },
  {
    path: '/404',
    name: 'NotFound',
    component: () => import('@/views/error/NotFound.vue'),
    meta: { title: '404', layout: 'blank' },
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'CatchAll',
    component: () => import('@/views/error/NotFound.vue'),
    meta: { title: '404', layout: 'blank' },
    beforeEnter: (to, _from, next) => {
      // redirect 会在解析阶段跳过 beforeEnter，导致未登录绕过守卫直接渲染 404。
      // 在守卫内跳转：未登录 → 登录页；已登录 → 渲染 404。
      if (!isLoggedIn()) return next(`/login?redirect=${to.fullPath}`)
      return next()
    },
  },
]

/** 根路由（登录后的框架） */
export const rootRoute: RouteRecordRaw = {
  path: '/',
  name: 'Layout',
  component: () => import('@/layouts/default/index.vue'),
  redirect: '/dashboard',
  children: [...v2HiddenRoutes],
}
