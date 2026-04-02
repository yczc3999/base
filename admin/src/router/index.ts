import { createRouter, createWebHashHistory } from 'vue-router'
import { staticRoutes, rootRoute } from './static'
import { setupGuard } from './guard'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [...staticRoutes, rootRoute],
})

setupGuard(router)

export default router
