import { describe, expect, it, vi } from 'vitest'
import type { CursorPage } from '@/api/v2/types'

vi.mock('@/api/request', () => ({
  isRequestCanceled: (error: { name?: string }) => error?.name === 'AbortError',
}))
import {
  createPageAnchor,
  isForbiddenV2Error,
  keepPreviousCursorPage,
  reconcilePageAnchor,
  v2PermissionForDomain,
} from '../page'
import { normalizeFilters, v2QueryKeys } from '../queryKeys'

const AS_OF = '2026-08-12T00:00:00Z'

function page(items: string[]): CursorPage<{ id: string }> {
  return {
    items: items.map((id) => ({ id })),
    next_cursor: 'next',
    has_more: true,
    as_of: AS_OF,
    filter_hash: 'a'.repeat(64),
  }
}

describe('v2QueryKeys', () => {
  it('contains domain, exact endpoint, normalized filters, direction, limit, cursor and asOf', () => {
    const key = v2QueryKeys.page(
      'evaluation',
      'labels',
      { state: 'OPEN' },
      'desc',
      50,
      'cursor',
      AS_OF,
    )
    expect(key).toEqual([
      'v2-admin-read',
      'evaluation',
      'labels',
      { state: 'OPEN' },
      'desc',
      50,
      'cursor',
      AS_OF,
    ])
  })

  it('canonicalizes key order and equivalent wire values', () => {
    expect(normalizeFilters({ neg_risk: true, closed: 'false' })).toEqual(
      normalizeFilters({ closed: false, neg_risk: 'true' }),
    )
  })

  it('isolates domains and concrete endpoints', () => {
    const intents = v2QueryKeys.page('execution', 'intents', {}, 'desc', 50, null, null)
    const orders = v2QueryKeys.page('execution', 'orders', {}, 'desc', 50, null, null)
    const episodes = v2QueryKeys.page('episodes', 'list', {}, 'desc', 50, null, null)
    expect(intents).not.toEqual(orders)
    expect(intents).not.toEqual(episodes)
  })
})

describe('cursor/asOf invalidation', () => {
  it('drops cursor and asOf atomically when filter identity changes', () => {
    const current = createPageAnchor('closed=false', 'old-cursor', AS_OF)
    expect(reconcilePageAnchor(current, 'closed=true', 'old-cursor', AS_OF)).toEqual({
      identity: 'closed=true',
      cursor: null,
      asOf: null,
    })
  })

  it('fails closed when cursor and asOf are not supplied as a pair', () => {
    expect(createPageAnchor('same', 'cursor', null)).toEqual({
      identity: 'same',
      cursor: null,
      asOf: null,
    })
  })
})

describe('placeholder isolation', () => {
  it('keeps the first page only while loading its next cursor in the same snapshot', () => {
    const previousData = page(['first-filter'])
    const previousKey = v2QueryKeys.page('markets', 'list', { closed: false }, 'desc', 50, null, null)
    const currentKey = v2QueryKeys.page(
      'markets',
      'list',
      { closed: false },
      'desc',
      50,
      'next',
      AS_OF,
    )
    expect(keepPreviousCursorPage(previousData, { queryKey: previousKey }, currentKey)).toBe(previousData)
  })

  it('never flashes data from the previous filter', () => {
    const previousData = page(['closed=false'])
    const previousKey = v2QueryKeys.page('markets', 'list', { closed: false }, 'desc', 50, null, null)
    const currentKey = v2QueryKeys.page('markets', 'list', { closed: true }, 'desc', 50, null, null)
    expect(keepPreviousCursorPage(previousData, { queryKey: previousKey }, currentKey)).toBeUndefined()
  })
})

describe('permission/error state mapping', () => {
  it('maps every UI domain to the exact server permission', () => {
    expect(v2PermissionForDomain('markets')).toBe('v2:markets:view')
    expect(v2PermissionForDomain('ai')).toBe('v2:ai:view')
    expect(v2PermissionForDomain('configuration')).toBe('v2:config:view')
    expect(v2PermissionForDomain('artifacts')).toBe('v2:artifact:read')
    expect(v2PermissionForDomain('unknown')).toBeNull()
  })

  it('turns transport and envelope 403 errors into the denied state', () => {
    expect(isForbiddenV2Error({ response: { status: 403 } })).toBe(true)
    expect(isForbiddenV2Error({ code: 403, status: 200 })).toBe(true)
    expect(isForbiddenV2Error({ response: { status: 500 } })).toBe(false)
  })
})
