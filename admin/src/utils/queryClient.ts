import { QueryClient } from '@tanstack/vue-query'

/**
 * 全局 TanStack Query 客户端。
 * staleTime 30s：页面切换/聚焦后 30 秒内的缓存直接复用，避免抖动；
 * retry 2：网络抖动自动重试；refetchOnWindowFocus 关闭：避免切页回来触发意外刷新。
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 30_000, retry: 2, refetchOnWindowFocus: false },
  },
})
