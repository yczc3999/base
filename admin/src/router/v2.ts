import type { RouteRecordRaw } from 'vue-router'

/**
 * V2 detail routes are static-but-hidden so a permitted user can follow a list
 * drill-down even when Base omits invisible menu rows from the menu response.
 * API authorization remains the data boundary; PageState renders denied on 403.
 */
export const v2HiddenRoutes: RouteRecordRaw[] = [
  {
    path: '/v2/tags',
    name: 'v2-page-tags',
    component: () => import('@/views/v2/tags/index.vue'),
    meta: { title: 'Tags', visible: false, permission: 'v2:tags:view' },
  },
  {
    path: '/v2/markets/:id',
    name: 'v2-page-market-detail',
    component: () => import('@/views/v2/markets/detail.vue'),
    meta: { title: 'Market Detail', visible: false, permission: 'v2:markets:view' },
  },
  {
    path: '/v2/components/:id',
    name: 'v2-page-component-detail',
    component: () => import('@/views/v2/components/detail.vue'),
    meta: { title: 'Component Detail', visible: false, permission: 'v2:components:view' },
  },
  {
    path: '/v2/episodes/:id',
    name: 'v2-page-episode-detail',
    component: () => import('@/views/v2/episodes/detail.vue'),
    meta: { title: 'Episode Detail', visible: false, permission: 'v2:episodes:view' },
  },
  {
    path: '/v2/decisions/:id',
    name: 'v2-page-decision-detail',
    component: () => import('@/views/v2/decisions/detail.vue'),
    meta: { title: 'Decision Detail', visible: false, permission: 'v2:decisions:view' },
  },
  {
    path: '/v2/ai-invocations/:id',
    name: 'v2-page-ai-detail',
    component: () => import('@/views/v2/ai-invocations/detail.vue'),
    meta: { title: 'AI Invocation Detail', visible: false, permission: 'v2:ai:view' },
  },
  {
    path: '/v2/artifacts/:content_hash',
    name: 'v2-page-artifacts',
    component: () => import('@/views/v2/artifacts/detail.vue'),
    meta: { title: 'Artifact Metadata', visible: false, permission: 'v2:artifact:read' },
  },
]
