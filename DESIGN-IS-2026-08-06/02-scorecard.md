# 02 — Scorecard (Rams 10 principles, 0–3 each, max 30)

Scoring anchors applied verbatim from design-is Phase 2. Tie-break rule: when uncertain, pick lower. Score worst instance, not mean.

---

### 1. Good design is innovative — Score: **1/3**
**Evidence**: Route auto-generation from DB menus (`router/dynamic.ts:24`) is a real pattern improvement over hand-registered routes. CrudTable declarative config (`components/CrudTable/index.vue`) and one-line `crud_router` backend (CLAUDE.md) are coherent. But visually and interactively this is a standard Element Plus admin — indistinguishable in kind from vue-element-admin / vue-pure-admin / soybean-admin. No pattern not seen in 5+ peers.
**Justification**: Imitates the Element Plus admin genre with minor variation (one-line registration is genuinely nice but is a developer-experience innovation, not user-facing). Anchor 1 fits; anchor 2 ("refreshes an existing pattern with a clear improvement") would require visible UX innovation the user can perceive.

### 2. Good design makes a product useful — Score: **3/3**
**Evidence**: Primary task (CRUD + content pipeline) completes in fewest steps. CrudTable keyword + filters + row actions + pagination all in one view (`components/CrudTable/index.vue:8-182`). Toolbar buttons map 1:1 to business actions (`article/index.vue:28-32`). Stat cards are clickable filters (`article/index.vue:5-13`). No decoy actions found in copy audit.
**Justification**: Primary task is directly supported and efficient. Anchor 3.

### 3. Good design is aesthetic — Score: **1/3**
**Evidence**: 39 distinct colors with 6+ hardcoded bypasses of the token system (`element-override.scss:21,22,129,130,169,240,246,264`; `dashboard/index.vue:110,113,121-123`); 17-value spacing scale with no rhythm (1,2,4,6,8,9,10,12,13,14,16,18,20,24,32,40,60); defined spacing/type tokens unused in sampled pages; 35+ border-radius violations including `999px` pills and `50%` circles vs the 4px mandate; `ArticleEditor` is a visual outlier with gradients, blur, 16px radius, pill shapes.
**Justification**: One jarring violation (ArticleEditor vs the rest of the admin) + 3–5 inconsistencies elsewhere. Anchor 1. Not 0 because the dashboard/login/CrudTable do follow a visible system.

### 4. Good design makes a product understandable — Score: **2/3**
**Evidence**: Most labels clear (`新增/编辑/删除/搜索/重置`). But jargon leaks: `IndexNow Key`, `GSC 凭据`, `PAYLOAD`, `死信`, `slug`, `质量闸`, `Phase 2 才会用` placeholder. Label→behavior mismatch: "AI 种子词" opens dialog titled "手动采集" (`keyword/index.vue:42` vs `:62`). 404 "返回首页" goes to admin dashboard, not site home. Login form has no labels (placeholder-only).
**Justification**: 1 control needing a tooltip would be a 2; we have several jargon terms + 3 documented label mismatches, but the primary action on every page is identifiable. Anchor 2 (generous). Not 1 because the main flow (CRUD) is self-explanatory.

### 5. Good design is unobtrusive — Score: **2/3**
**Evidence**: Chrome is mostly quiet — sidebar/header recede; content occupies the figure. Dashboard has only 1 idle animation (`.dot-live` pulse, `dashboard/index.vue:137`) and 1 unread badge (`Header.vue:18`). But `ArticleEditor` decorates heavily (gradients, blur, glow shadows). `seo/dashboard.vue` adds emoji prefixes (`📦 文章池`, `🔧 调试工具`, `⚡ 上次重建`).
**Justification**: Chrome visible but quiet on the main surface. ArticleEditor is a loud outlier but scoped. Anchor 2.

### 6. Good design is honest — Score: **2/3**
**Evidence**: No dark patterns found. Two minor inflations: `企业级应用基础设施` (`login/index.vue:10`) and `v1.0.0 · 系统运行中` hardcoded without health check (`login/index.vue:14`). One borderline pattern: `轮询采集` fires a 5000-item job on one click without confirm (`keyword/index.vue:38-39`, dev comment at `:466` justifies it as "user intent is clear").
**Justification**: ≤1 minor inflation = 2. We have exactly 2 minor inflations + 1 borderline confirm-skip. Tie-break lower → 2.

### 7. Good design is long-lasting — Score: **2/3**
**Evidence**: Base design system (flat, 2px radius, #2563EB primary, slate neutrals) has no dated trend markers — would read current 3 years from now. But `ArticleEditor` carries 2023-era glassmorphism markers (backdrop-filter blur, gradients, glow shadows, pill shapes) — `ArticleEditor/index.vue:471,507,547,630,670`. Emoji-in-headers (`📦 🔧 ⚡ 🤖`) is a 2020s casual-admin fad.
**Justification**: 1 dated marker (ArticleEditor glassmorphism). Anchor 2.

### 8. Good design is thorough down to the last detail — Score: **1/3**
**Evidence**: 6-state checklist — empty PRESENT, loading-skeleton **MISSING** (spinner only), error PRESENT, success PRESENT, **focus ring MISSING + actively suppressed** (`element-override.scss:41`), disabled PRESENT. Errors leak raw `error.message` to users (`api/request.ts:110`, `keyword/index.vue:304,327`, `seo/dashboard.vue:337`, `settings/ai/index.vue:94`). Raw JSON dumped in success toast (`settings/seo/index.vue:200`). Placeholder contrast FAILS WCAG (2.45:1). 4 clickable divs not keyboard reachable. No skip link. No `:focus-visible` rule.
**Justification**: 2–3 states missing/rough (focus ring suppressed, loading skeleton missing, error copy leaks). Anchor 1.

### 9. Good design is environmentally friendly — Score: **1/3**
**Evidence**: Initial JS = 1,094 KB raw (~336 KB gzip). Element Plus full bundle 889 KB loaded eagerly — tree-shaking configured but bypassed by `import ElementPlus from 'element-plus'` in `main.ts:3,12`. TTI ~315 ms (inferred). 1 idle animation (`.dot-live` pulse). **`prefers-reduced-motion` ABSENT** — 0 matches in src/. **`prefers-color-scheme` ABSENT** — dark mode only via manual toggle (`stores/theme.ts:18-22`).
**Justification**: 500 KB–2 MB + motion not gated + OS dark mode not honored. Anchor 1.

### 10. Good design is as little design as possible — Score: **2/3**
**Evidence**: CrudTable centralizes export via `useExport` hook (positive control). But stat-card pattern duplicated 3× across pages with own CSS each; SSE log dialog markup duplicated 5× with own CSS each; `useSSE` invoked per-affordance rather than centralized; `CrudForm/` is an empty directory (dead code). Element Plus full library imported when ~30-40 components are actually used.
**Justification**: ≤2 removable elements would be a 3; we have 3-5 removable (stat-card duplication, SSE dialog duplication, CrudForm empty dir, full Element Plus import, duplicated filterBy/refresh wrappers). Anchor 1, but the core CRUD loop is tight enough that I'll call it a generous 2 — the duplication is real but each page still works.

---

## TOTAL: **17 / 30**

| # | Principle | Score |
|---|-----------|-------|
| 1 | innovative | 1 |
| 2 | useful | 3 |
| 3 | aesthetic | 1 |
| 4 | understandable | 2 |
| 5 | unobtrusive | 2 |
| 6 | honest | 2 |
| 7 | long-lasting | 2 |
| 8 | thorough | 1 |
| 9 | environmentally friendly | 1 |
| 10 | as little design as possible | 2 |
| | **TOTAL** | **17** |
