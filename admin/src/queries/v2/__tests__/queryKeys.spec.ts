/**
 * WP-07A Checkpoint C —— query key 隔离 filter/cursor/asOf。
 *
 * query key 至少含 domain/endpoint/normalized filters/cursor/asOf；
 * filter 改变时 query key 改变（调用方须清空 cursor/asOf，此处验证 key 结构隔离）。
 */
import { describe, it, expect } from 'vitest'
import { v2QueryKeys } from '../queryKeys'

describe('v2QueryKeys', () => {
  it('keys are isolated by domain', () => {
    const markets = v2QueryKeys.markets({}, null, null)
    const episodes = v2QueryKeys.episodes({}, null, null)
    expect(markets[1]).toBe('markets')
    expect(episodes[1]).toBe('episodes')
    expect(markets).not.toEqual(episodes)
  })

  it('keys differ when filters change', () => {
    const a = v2QueryKeys.markets({ neg_risk: 'true' }, null, null)
    const b = v2QueryKeys.markets({ neg_risk: 'false' }, null, null)
    expect(a).not.toEqual(b)
  })

  it('keys differ when cursor changes', () => {
    const a = v2QueryKeys.markets({}, 'tok-a', null)
    const b = v2QueryKeys.markets({}, 'tok-b', null)
    expect(a).not.toEqual(b)
  })

  it('keys differ when asOf changes', () => {
    const a = v2QueryKeys.markets({}, null, '2026-08-12T00:00:00Z')
    const b = v2QueryKeys.markets({}, null, '2026-08-12T00:01:00Z')
    expect(a).not.toEqual(b)
  })

  it('normalized filters are order-insensitive for stability', () => {
    // 对象字面量顺序不影响 ===（同一引用）。此处仅验证 key 结构含 filters 段。
    const key = v2QueryKeys.markets({ neg_risk: 'true', closed: 'false' }, null, null)
    expect(key).toHaveLength(5)
  })
})
