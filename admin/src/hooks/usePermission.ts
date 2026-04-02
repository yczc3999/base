import { useUserStore } from '@/stores/user'

/** 权限判断 */
export function usePermission() {
  const userStore = useUserStore()

  function hasPerms(perms: string | string[]): boolean {
    return userStore.hasPerms(perms)
  }

  return { hasPerms }
}
