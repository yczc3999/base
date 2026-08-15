import { describe, expect, it } from 'vitest'
import { stripEmptyFilters, useKeysetList } from '../_shared/useKeysetList'

describe('stripEmptyFilters', () => {
  it('drops empty strings and keeps real values', () => {
    expect(stripEmptyFilters({ slug: '', disposition: 'SELECT', seen: '' })).toEqual({
      disposition: 'SELECT',
    })
  })
})

describe('useKeysetList', () => {
  it('applies filters only on search and rewinds to page 1', () => {
    const list = useKeysetList()
    list.next({ next_cursor: 'c1', as_of: 't1', has_more: true })
    expect(list.page.value).toBe(2)
    list.applyFilters({ slug: 'politics', disposition: '' })
    expect(list.applied.value).toEqual({ slug: 'politics' })
    expect(list.cursor.value).toBeNull()
    expect(list.asOf.value).toBeNull()
    expect(list.page.value).toBe(1)
  })

  it('reset clears filters and cursor stack', () => {
    const list = useKeysetList()
    list.applyFilters({ slug: 'x' })
    list.next({ next_cursor: 'c1', as_of: 't1', has_more: true })
    list.resetFilters()
    expect(list.applied.value).toEqual({})
    expect(list.page.value).toBe(1)
    expect(list.canPrev.value).toBe(false)
  })

  it('prev walks the cursor stack; next is a no-op without has_more', () => {
    const list = useKeysetList()
    list.next({ next_cursor: 'c1', as_of: 't1', has_more: false })
    expect(list.cursor.value).toBeNull()
    list.next({ next_cursor: 'c1', as_of: 't1', has_more: true })
    list.next({ next_cursor: 'c2', as_of: 't1', has_more: true })
    expect(list.cursor.value).toBe('c2')
    expect(list.page.value).toBe(3)
    list.prev()
    expect(list.cursor.value).toBe('c1')
    list.prev()
    expect(list.cursor.value).toBeNull()
    list.prev()
    expect(list.page.value).toBe(1)
  })

  it('page size change returns to the first page', () => {
    const list = useKeysetList()
    list.next({ next_cursor: 'c1', as_of: 't1', has_more: true })
    list.setLimit(50)
    expect(list.limit.value).toBe(50)
    expect(list.page.value).toBe(1)
    expect(list.cursor.value).toBeNull()
  })
})
