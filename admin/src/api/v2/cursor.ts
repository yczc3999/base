/** V2 Admin keyset cursor helpers（WP-07A Checkpoint C）。

opaque cursor 不在浏览器解码/重签；只做透传与基础校验（长度上限、显式字符串）。
*/

import type { OpaqueCursor } from './types'

/** 服务端签名 token 的最大长度（与 serve/app/db/cursor.py 对齐） */
export const MAX_CURSOR_CHARS = 2048

/**
 * 校验 cursor 为合法 opaque token（只透传，不解码/重签）。
 * - 非字符串 / 超长 → 返回 null（调用方按无 cursor 处理或报错）。
 * - 浏览器不解析 payload，不校验签名。
 */
export function normalizeCursor(raw: unknown): OpaqueCursor | null {
  if (typeof raw !== 'string' || raw.length === 0) return null
  if (raw.length > MAX_CURSOR_CHARS) return null
  return raw
}

/** 翻页：若 limit 变化，cursor 不变（limit 不进 cursor 身份）。 */
export function buildPageParams(
  base: Record<string, unknown>,
  cursor: OpaqueCursor | null,
  limit?: number,
  direction?: 'asc' | 'desc',
): Record<string, unknown> {
  const params: Record<string, unknown> = { ...base }
  if (limit !== undefined) params.limit = String(limit)
  if (direction !== undefined) params.direction = direction
  if (cursor) params.cursor = cursor
  return params
}
