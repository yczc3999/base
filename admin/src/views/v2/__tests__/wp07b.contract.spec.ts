import { existsSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { rootRoute } from '@/router/static'
import { v2HiddenRoutes } from '@/router/v2'

const ADMIN_ROOT = fileURLToPath(new URL('../../../../', import.meta.url))
const source = (relative: string) => readFileSync(resolve(ADMIN_ROOT, relative), 'utf8')

const LIST_VIEWS = [
  'markets', 'tags', 'components', 'episodes', 'decisions', 'execution', 'models-ai',
  'ai-invocations', 'costs', 'config', 'releases', 'evaluation', 'replay', 'integrity',
].map((name) => `src/views/v2/${name}/index.vue`)

const ROUTE_TEMPLATES = [
  'dashboard/index', 'markets/index', 'tags/index', 'components/index', 'episodes/index',
  'decisions/index', 'execution/index', 'models-ai/index', 'ai-invocations/index',
  'costs/index', 'config/index', 'releases/index', 'evaluation/index', 'replay/index',
  'integrity/index', 'markets/detail', 'components/detail', 'episodes/detail',
  'decisions/detail', 'ai-invocations/detail', 'artifacts/detail',
]

describe('WP-07B route and view contracts', () => {
  it('registers hidden drill-down routes under the authenticated root', () => {
    expect(v2HiddenRoutes.map((route) => route.path)).toEqual([
      '/markets/:id',
      '/components/:id',
      '/episodes/:id',
      '/decisions/:id',
      '/ai-invocations/:id',
      '/artifacts/:content_hash',
    ])
    expect(rootRoute.children?.map((route) => route.path).slice(0, 6)).toEqual([
      '/markets/:id',
      '/components/:id',
      '/episodes/:id',
      '/decisions/:id',
      '/ai-invocations/:id',
      '/artifacts/:content_hash',
    ])
    expect(new Set(v2HiddenRoutes.map((route) => route.name)).size).toBe(6)
  })

  it('has a concrete Vue component for every seeded route template', () => {
    for (const template of ROUTE_TEMPLATES) {
      expect(existsSync(resolve(ADMIN_ROOT, `src/views/v2/${template}.vue`)), template).toBe(true)
    }
  })

  it('loads V2 tokens and scopes every V2 business page to them', () => {
    expect(source('src/styles/index.scss')).toContain("@import './v2-tokens.scss';")
    for (const template of ROUTE_TEMPLATES) {
      expect(source(`src/views/v2/${template}.vue`), template).toContain('class="v2-page"')
    }
  })
})

describe('WP-07B keyset and state wiring', () => {
  it('passes reactive refs instead of setup-time .value snapshots on every keyset page', () => {
    for (const view of LIST_VIEWS) {
      const text = source(view)
      expect(text, view).not.toMatch(/\b(?:filters|cursor|asOf|limit):\s*[A-Za-z0-9_]+\.value/)
      expect(text, view).toContain('next_cursor')
      expect(text, view).toContain('as_of')
    }
  })

  it('does not hard-code denied off and exposes retry plus additive partial content', () => {
    for (const template of ROUTE_TEMPLATES) {
      expect(source(`src/views/v2/${template}.vue`), template).not.toContain(':denied="false"')
    }
    const state = source('src/views/v2/_shared/PageState.vue')
    expect(state).toContain("defineEmits<{ (e: 'retry'): void }>()")
    expect(state).toContain("@click=\"emit('retry')\"")
    expect(state.indexOf('data-testid="v2-partial"')).toBeLessThan(state.indexOf('<slot v-else />'))
  })

  it('carries the AI composite identity through the list drill-down', () => {
    const list = source('src/views/v2/ai-invocations/index.vue')
    const detail = source('src/views/v2/ai-invocations/detail.vue')
    expect(list).toContain("query: { occurred_at: row.occurred_at }")
    expect(detail).toContain("route.query.occurred_at")
    expect(detail).toContain('enabled: computed(() => !missingOccurredAt.value)')
  })

  it('wires the supported business filters and all Integrity tabs', () => {
    expect(source('src/views/v2/execution/index.vue')).toContain("field: 'status'")
    expect(source('src/views/v2/evaluation/index.vue')).toContain("field: 'state'")
    expect(source('src/views/v2/costs/index.vue')).toContain("field: 'cost_kind'")
    expect(source('src/views/v2/config/index.vue')).toContain("field: 'status'")
    expect(source('src/views/v2/releases/index.vue')).toContain("field: 'status'")
    const integrity = source('src/views/v2/integrity/index.vue')
    expect(integrity).toContain('label="Alerts" name="alerts"')
    expect(integrity).toContain('label="Workflows" name="workflows"')
    expect(integrity).toContain('label="External Calls" name="external-calls"')
    expect(integrity).toContain('useIntegrityWorkflow')
  })

  it('does not label an episode id as a decision id', () => {
    const episode = source('src/views/v2/episodes/detail.vue')
    expect(episode).not.toMatch(/k:\s*'decision'\s*,\s*v:\s*ep\.id/)
    expect(episode).toContain("k: 'decision_opportunity_id'")
  })

  it('uses real artifact metadata routes instead of inert hash text', () => {
    for (const view of [
      'markets/detail', 'components/detail', 'episodes/detail', 'decisions/detail',
      'ai-invocations/detail', 'config/index', 'models-ai/index', 'replay/index',
    ]) {
      expect(source(`src/views/v2/${view}.vue`), view).toContain('ArtifactLink')
    }
    expect(source('src/views/v2/_shared/ArtifactLink.vue')).toContain('`/artifacts/${contentHash}`')
  })
})

describe('WP-07B read-only boundary', () => {
  it('does not call mutations or inline artifact bytes from V2 views', () => {
    const combined = ROUTE_TEMPLATES
      .map((template) => source(`src/views/v2/${template}.vue`))
      .join('\n')
    expect(combined).not.toMatch(/\b(?:post|put|patch|del|useMutation)\s*\(/)
    expect(combined).not.toContain('useArtifactContent')
    expect(combined).not.toContain('fetchArtifactContent')
  })
})
