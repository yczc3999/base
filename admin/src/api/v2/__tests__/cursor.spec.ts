/**
 * WP-07A Checkpoint C —— v2 cursor 逻辑单元。
 *
 * opaque cursor 不在浏览器解码/重签；只透传 + 长度/类型校验；
 * limit 不进 cursor 身份（改变 limit 不改变 cursor）。
 */
import { describe, it, expect } from 'vitest'
import { buildPageParams, normalizeCursor, MAX_CURSOR_CHARS } from '../cursor'

describe('normalizeCursor', () => {
  it('passes through a valid opaque token', () => {
    expect(normalizeCursor('abc.def')).toBe('abc.def')
  })

  it('rejects empty / non-string / oversized', () => {
    expect(normalizeCursor('')).toBeNull()
    expect(normalizeCursor(null)).toBeNull()
    expect(normalizeCursor(undefined)).toBeNull()
    expect(normalizeCursor(123 as unknown)).toBeNull()
    expect(normalizeCursor('x'.repeat(MAX_CURSOR_CHARS + 1))).toBeNull()
  })
})

describe('buildPageParams', () => {
  it('keeps cursor when limit changes (limit not in cursor identity)', () => {
    const withLimit7 = buildPageParams({}, 'tok', 7, 'desc')
    const withLimit13 = buildPageParams({}, 'tok', 13, 'desc')
    expect(withLimit7.cursor).toBe('tok')
    expect(withLimit13.cursor).toBe('tok')
    expect(withLimit7.limit).toBe(7)
    expect(withLimit13.limit).toBe(13)
  })

  it('omits cursor when null', () => {
    const params = buildPageParams({}, null, 50, 'desc')
    expect('cursor' in params).toBe(false)
  })
})
