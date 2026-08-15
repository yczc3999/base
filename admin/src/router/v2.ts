import type { RouteRecordRaw } from 'vue-router'

/**
 * 详情页静态注册：Base 菜单对 is_visible=false 的行不生成侧栏，但仍要能下钻。
 * 列表页由菜单 path 生成（``#/markets``），不再单独开 ``/v2/`` 分组。
 */
export const v2HiddenRoutes: RouteRecordRaw[] = [
  {
    path: '/markets/:id',
    name: 'v2-page-market-detail',
    component: () => import('@/views/v2/markets/detail.vue'),
    meta: { title: '市场详情', visible: false, permission: 'v2:markets:view' },
  },
  {
    path: '/components/:id',
    name: 'v2-page-component-detail',
    component: () => import('@/views/v2/components/detail.vue'),
    meta: { title: '组件详情', visible: false, permission: 'v2:components:view' },
  },
  {
    path: '/episodes/:id',
    name: 'v2-page-episode-detail',
    component: () => import('@/views/v2/episodes/detail.vue'),
    meta: { title: '回合详情', visible: false, permission: 'v2:episodes:view' },
  },
  {
    path: '/decisions/:id',
    name: 'v2-page-decision-detail',
    component: () => import('@/views/v2/decisions/detail.vue'),
    meta: { title: '决策详情', visible: false, permission: 'v2:decisions:view' },
  },
  {
    path: '/ai-invocations/:id',
    name: 'v2-page-ai-detail',
    component: () => import('@/views/v2/ai-invocations/detail.vue'),
    meta: { title: 'AI 调用详情', visible: false, permission: 'v2:ai:view' },
  },
  {
    path: '/artifacts/:content_hash',
    name: 'v2-page-artifacts',
    component: () => import('@/views/v2/artifacts/detail.vue'),
    meta: { title: '制品', visible: false, permission: 'v2:artifact:read' },
  },
]

/** 旧 ``#/v2/...`` 书签落到与 Base 相同的 hash 路径。 */
export const v2LegacyRedirects: RouteRecordRaw[] = [
  { path: '/v2/dashboard', redirect: '/overview' },
  { path: '/v2/markets', redirect: '/markets' },
  { path: '/v2/tags', redirect: '/tags' },
  { path: '/v2/components', redirect: '/components' },
  { path: '/v2/episodes', redirect: '/episodes' },
  { path: '/v2/decisions', redirect: '/decisions' },
  { path: '/v2/execution', redirect: '/execution' },
  { path: '/v2/models-ai', redirect: '/models-ai' },
  { path: '/v2/ai-invocations', redirect: '/ai-invocations' },
  { path: '/v2/costs', redirect: '/costs' },
  { path: '/v2/config', redirect: '/config' },
  { path: '/v2/releases', redirect: '/releases' },
  { path: '/v2/evaluation', redirect: '/evaluation' },
  { path: '/v2/replay', redirect: '/replay' },
  { path: '/v2/integrity', redirect: '/integrity' },
  { path: '/v2/markets/:id', redirect: (to) => `/markets/${to.params.id}` },
  { path: '/v2/components/:id', redirect: (to) => `/components/${to.params.id}` },
  { path: '/v2/episodes/:id', redirect: (to) => `/episodes/${to.params.id}` },
  { path: '/v2/decisions/:id', redirect: (to) => `/decisions/${to.params.id}` },
  { path: '/v2/ai-invocations/:id', redirect: (to) => `/ai-invocations/${to.params.id}` },
  { path: '/v2/artifacts/:content_hash', redirect: (to) => `/artifacts/${to.params.content_hash}` },
]
