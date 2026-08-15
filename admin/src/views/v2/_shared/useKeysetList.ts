import { computed, ref } from 'vue'

/** 列表查询用的 keyset 页游标。空字符串不当成筛选值。 */
export type KeysetPageHint = {
  next_cursor?: string | null
  as_of?: string | null
  has_more?: boolean
}

export function stripEmptyFilters(raw: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = {}
  for (const [key, value] of Object.entries(raw)) {
    if (value !== '' && value != null) out[key] = value
  }
  return out
}

export function useKeysetList(defaultLimit = 20) {
  const applied = ref<Record<string, string>>({})
  const cursor = ref<string | null>(null)
  const asOf = ref<string | null>(null)
  const limit = ref(defaultLimit)
  const stack = ref<{ cursor: string | null; asOf: string | null }[]>([
    { cursor: null, asOf: null },
  ])

  const page = computed(() => stack.value.length)
  const canPrev = computed(() => stack.value.length > 1)

  function rewind() {
    cursor.value = null
    asOf.value = null
    stack.value = [{ cursor: null, asOf: null }]
  }

  function applyFilters(raw: Record<string, string>) {
    applied.value = stripEmptyFilters(raw)
    rewind()
  }

  function resetFilters() {
    applied.value = {}
    rewind()
  }

  function next(hint: KeysetPageHint | null | undefined) {
    if (!hint?.has_more || !hint.next_cursor) return
    const nextAsOf = hint.as_of ?? null
    stack.value = [...stack.value, { cursor: hint.next_cursor, asOf: nextAsOf }]
    cursor.value = hint.next_cursor
    asOf.value = nextAsOf
  }

  function prev() {
    if (stack.value.length <= 1) return
    const nextStack = stack.value.slice(0, -1)
    stack.value = nextStack
    const top = nextStack[nextStack.length - 1]
    cursor.value = top.cursor
    asOf.value = top.asOf
  }

  function setLimit(size: number) {
    if (!Number.isFinite(size) || size < 1) return
    limit.value = size
    rewind()
  }

  return {
    applied,
    cursor,
    asOf,
    limit,
    page,
    canPrev,
    applyFilters,
    resetFilters,
    next,
    prev,
    setLimit,
    rewind,
  }
}
