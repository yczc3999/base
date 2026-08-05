/**
 * 数据字典 API — 公开端点（无 auth）+ 模块级缓存
 *
 * DictTag 组件 / 业务页面共用。同一 type 只请求一次，跨组件共享。
 */
import { get } from './request'

export interface DictItemOption {
  value: string
  label: string
}

const cache = new Map<string, DictItemOption[]>()

export async function getDictItems(type: string): Promise<DictItemOption[]> {
  if (!type) return []
  const cached = cache.get(type)
  if (cached) return cached
  try {
    const items: DictItemOption[] = (await get('/dict/items', { type })) || []
    cache.set(type, items)
    return items
  } catch {
    return []
  }
}

/** 按值取标签（未找到返回 undefined） */
export async function getDictLabel(type: string, value: any): Promise<string | undefined> {
  const items = await getDictItems(type)
  return items.find((i) => String(i.value) === String(value))?.label
}

/** 手动清空某类型缓存（管理端改字典后调用） */
export function clearDictCache(type?: string) {
  if (type) cache.delete(type)
  else cache.clear()
}
