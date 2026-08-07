# 01 — Evidence Consolidation

5 subagents × /code/base/admin. All citations verbatim from subagent reports.

---

## A. Structural evidence (subagent 1)

- **Interactive elements on article CRUD page**: 21 fixed + 2 per row × R rows.
  - 3 stat cards clickable, 2 search inputs, 2 selects, 2 search buttons, 5 toolbar buttons, 3 sortable headers, 4 pagination controls — `views/content/article/index.vue:5-32,197-207`, `components/CrudTable/index.vue:8-182`
- **Max component nesting**: 6 authored levels (Layout → AppMain → RouterView → ArticlePage → CrudTable → ElTable → cell renderer) — `layouts/default/index.vue:3-7` → `components/CrudTable/index.vue:116-148`
- **Repeated patterns**:
  - stat-card pattern duplicated 3× (`dashboard/index.vue:6-14`, `article/index.vue:4-21`, `keyword/index.vue:4-25`) — no shared component
  - SSE log dialog markup duplicated 5× (article 3×, keyword 2×) with own CSS each
  - `useSSE` invoked per-affordance, not centralized (article 3×, keyword 2×)
  - Positive control: export IS centralized via `useExport.ts`
- **Dead imports/props**: 2 unused imports in `article/index.vue:175,180` (`reactive`, `post`); `components/CrudForm/` is empty dir
- **Route generation**: fully declarative, 0 lines per new page; menu DB row → `router/dynamic.ts:24` auto-maps

## B. Visual evidence (subagent 2)

- **Spacing scale**: 17 distinct px values (1,2,4,6,8,9,10,12,13,14,16,18,20,24,32,40,60). Token scale `--space-xs..2xl` defined in `variables.scss:52-58` but **not consumed** in the 4 sampled pages (only layout files use it).
- **Type scale**: 12 distinct px values (10,11,12,13,14,16,18,24,26,28,42,48). Token scale `--text-xs..3xl` defined `variables.scss:63-69` but `--text-lg/xl/3xl` unused in observed pages.
- **Distinct colors**: **39 unique values**. 21 light tokens + 12 dark tokens + 6 hardcoded bypasses (`element-override.scss:21,22,129,130,169,240,246,264`; `dashboard/index.vue:110,113,121-123`; `article/index.vue:360`; `login/index.vue:206,225,233`).
- **Lowest contrast**: login brand-desc `#64748B` on sidebar `#0F172A` = **3.75:1** (FAIL body AA) — `login/index.vue:225`
- **States checklist (CrudTable + SettingForm)**:
  - Empty: PRESENT (Element default `暂无数据`)
  - Loading skeleton: **MISSING** (spinner overlay only, `CrudTable/index.vue:95`)
  - Error toast: PRESENT (`api/request.ts:104-111`)
  - Success feedback: PRESENT (`useCrud.ts:163,179,196`)
  - **Focus ring: MISSING + actively suppressed** (`element-override.scss:41` — `outline: none !important; box-shadow: none !important;` on `.el-button:focus`)
  - Disabled: PRESENT (Element defaults)
- **User's flat-preference compliance VIOLATIONS**:
  - `box-shadow` APPLIED in 11 places — `element-override.scss:129,130,169,240,246`; `index.scss:14`; `JsonEditor/index.vue:305`; `ArticleEditor/index.vue:483,572,635,665,717,816,836`
  - `linear-gradient` APPLIED 7 places — all in `ArticleEditor/index.vue:507,547,670,714,722,746,796`
  - `backdrop-filter: blur()` APPLIED — `ArticleEditor/index.vue:471,630`
  - `transform: scale/translate` APPLIED — `element-override.scss:40` (button :active), `transitions.scss:4,5,12,13`, `SettingForm/index.vue:256`, `ArticleEditor/index.vue:593,719,739,740,827`
- **Border-radius violations (>4px mandate)**: 35+ hits, all hardcoded. Worst: `ArticleEditor/index.vue` has `16px` (`:482,:664`), `999px` pill (`:501,:566,:569,:617,:813`), `14px` (`:635`), `12px` (`:713,:745,:759`), `10px` (`:514,:685,:697,:834`), `50%` circle (`:590,:681,:728,:822`). Also `seo/dashboard.vue` `999px` pill at `:563,:570`, `ImportModal/index.vue:136` `50%`, plus 8px across `Header.vue:162`, `RichEditor/index.vue:77`, `seo/sitemap.vue:153`, `keyword/index.vue:640`, `article/index.vue:333,365,376`.

## C. Copy & honesty evidence (subagent 3)

- **Sampled 45+ user-facing strings** across login/dashboard/article/keyword/seo/settings/system
- **Inflations**:
  - `企业级应用基础设施` — `login/index.vue:10` ("enterprise-grade" claim, no backing on that surface)
  - `v1.0.0 · 系统运行中` — `login/index.vue:14` (hardcoded "system running" without health check)
- **Dark patterns**: NONE of the classics. Borderline: `轮询采集` (`keyword/index.vue:38-39`) starts long-running batch with no confirm — comment at `:466` says "用户点按钮就是意图明确"
- **Jargon flagged**: `IndexNow Key`, `GSC 凭据`, `Bing API Key`, `Phase 2 才会用` (placeholder), `slug`, `PAYLOAD`, `死信`, `索引健康度`, `质量闸`
- **Label→behavior mismatches**:
  1. "AI 种子词" button opens dialog titled "手动采集" — `keyword/index.vue:42` vs `:62`
  2. "下线" reverts to "candidate", not "offline" — `keyword/index.vue:445-454`
  3. 404 "返回首页" goes to admin dashboard, not site home — `NotFound.vue:6` → `router/static.ts:33`
- **Empty-state copy**: CrudTable default `暂无数据` NOT actionable. Actionable only in `seo/dashboard.vue:115-116` ("点「立即跑全链路」启动 worker") and `seo/sitemap.vue:18-19`. Other pages: dashboard `:31`, cache `:10`, monitor `:86`, session `:8`, task_monitor `:24`, trash `:17` — all NOT actionable.
- **Error-toast leaks**:
  - `api/request.ts:110` — surfaces `error.message` verbatim (e.g. "Request failed with status code 500")
  - `useExport.ts:42` — "未获取到任务标识" (internal jargon)
  - `keyword/index.vue:304,327`, `seo/dashboard.vue:337`, `settings/ai/index.vue:94` — `e?.message` verbatim
  - `settings/seo/index.vue:200` — `IndexNow 测试 OK: ${JSON.stringify(res)}` dumps raw JSON to user

## D. Weight & friction evidence (subagent 4)

- **Initial-load JS**: **1,120,245 bytes raw (~1,094 KB)** = entry 98,803 B + 10 modulepreloads. Largest single chunk: Element Plus full bundle `es-BP3qQLMS.js` = **889,458 B**.
- **Total JS files in dist**: 80
- **Tree-shaking**: **NOT enabled** — `main.ts:3,4,12` does `import ElementPlus from 'element-plus'` + `app.use(ElementPlus)` (full import). `vite.config.ts:12-20` has `ElementPlusResolver` configured but is bypassed by the full import.
- **TTI estimate**: ~315 ms after first paint (1,094 KB raw / ~5 MB/s parse ≈ 214 ms + network). INFERRED.
- **Animations on idle dashboard**:
  1. `.dot-live` pulse `animation: pulse 2s ease-in-out infinite` — `dashboard/index.vue:137,139`
  2. Route page-load `<transition name="fade-slide">` — `AppMain.vue:4`, `transitions.scss:1-2`
  3. NProgress top bar during navigation — `router/guard.ts:14,69`
- **Initial-load badges/modals**: 1 hand-rolled `.badge` span on Header bell (`Header.vue:18`) when unreadCount>0. 0 ElMessage/ElNotification/ElDialog on initial.
- **Dark mode**: manual toggle only (`stores/theme.ts:18-22`, `main.ts:5`); **`prefers-color-scheme` ABSENT** (0 matches in src/)
- **`prefers-reduced-motion`**: **ABSENT** (0 matches in src/)

## E. Accessibility evidence (subagent 5)

- **WCAG contrast FAILS** (body text < 4.5:1):
  - Secondary text `#64748B` on page bg `#F1F5F9` = 4.34:1 FAIL (`layout.scss:34-37`)
  - Placeholder `#94A3B8` on input `#F8FAFC` = 2.45:1 FAIL
  - White on primary `#3B82F6` = 3.68:1 FAIL for 13px body
  - White on danger `#EF4444` = 3.76:1 FAIL
  - Status badges: success 3.00 / warning 1.93 / info 4.39 — all FAIL (`CrudTable/index.vue:503-506`)
  - Login brand-desc `#64748B` on `#0F172A` = 3.75:1 FAIL
  - Dark-mode placeholder `#64748b` on `#0f172a` = 3.75:1 FAIL
- **Focus order**: correct tab order on login + CRUD page. **BUT 4 interactive elements unreachable by keyboard**:
  - stat-card `@click` divs (`article/index.vue:5,9,13,17`, `keyword/index.vue:5-13`)
  - TagsView tag-item divs (`TagsView.vue:3-13`)
  - Header user-menu dropdown trigger div (`Header.vue:41-45`)
  - Login captcha-SVG refresh div (`login/index.vue:64`)
- **ARIA landmarks**: exactly 1 on layout shell (`<nav>` in `Sidebar.vue:13`). No `<header>`, `<main>`, `<footer>` on Header/TagsView/AppMain.
- **Skip-link**: ABSENT
- **Focus-visible**: NO `:focus-visible` anywhere. `:focus` actively removed on buttons (`element-override.scss:41`).
- **Form labels**: Login form has NO labels (placeholder-only) — `login/index.vue:31-81`. CrudTable/SettingForm do bind labels via `el-form-item label=`.
- **Color-only info**: Dashboard system-status uses green `.green` with no text label (`dashboard/index.vue:81-82,133`); `.dot-live` green dot has no "OK" text (`dashboard/index.vue:38,135-138`).
