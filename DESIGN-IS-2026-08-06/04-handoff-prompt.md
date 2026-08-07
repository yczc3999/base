# 04 — Handoff prompt (ready for /make-plan)

```
/make-plan Refine Base Platform admin (/code/base/admin) based on a Dieter Rams audit (total 17/30).

Verdict paragraph (quoted from 03-verdict.md):
> The bones are load-bearing (#2 useful = 3, structure is solid, declarative CRUD architecture works) but the skin, the details, and the resource envelope are not at "精致" level. 乔布斯不会退货这个产品，但他会打回 3 个具体面：visual system 不严（39 色 + 17 间距 + 35 处圆角违规）、细节不达标（焦点环被主动抑制、加载骨架缺失、原始 error.message 直接吐给用户）、资源不节（Element Plus 全量 889 KB + 无 prefers-reduced-motion + 无 prefers-color-scheme）。

Keep (already strong, do NOT touch in this pass):
- Principle #2 (useful) scored 3 — Evidence: CrudTable centralizes CRUD in one view (components/CrudTable/index.vue:8-182); toolbar maps 1:1 to business actions (views/content/article/index.vue:28-32); stat cards are clickable filters (article/index.vue:5-13). Regression check: after refine, all 5 default CRUD ops (getList/getDetail/doEdit/doDelete/doExport) still work without extra clicks.
- Principle #4 (understandable) scored 2 — Evidence: primary action identifiable on every page; most labels plain Chinese. Keep this level — do not introduce new jargon while fixing the existing 12.
- Principle #5 (unobtrusive) scored 2 — Evidence: chrome quiet on dashboard/article; only 1 idle animation. Regression check: idle dashboard animations stay ≤1; badge count stays ≤1.
- Principle #6 (honest) scored 2 — Evidence: no dark patterns found in audit. Regression check: no new marketing superlative, no new confirm-skip on long-running actions.
- Principle #10 (as-little-design) scored 2 — Evidence: useExport hook is the positive-control centralization (hooks/useExport.ts consumed once by CrudTable/index.vue:62-69). Regression check: no new duplicated stat-card / SSE-dialog markup added.

Fix in priority order (top 5 moves from the audit, verbatim):
1. Principle #3 aesthetic — 设计令牌收口: 把 39 色 → ≤20（合并同义色），17 间距 → 8 标准档（4/8/12/16/24/32/48/64），12 字阶 → 7 标准档（12/13/14/16/18/24/32）；清掉 6 处硬编码 hex（src/styles/element-override.scss:21,22,129,130,169,240,246,264、src/views/dashboard/index.vue:110,113,121-123）；让定义了却没被消费的 --space-* / --text-lg/xl/3xl 真正接管所有页面。Evidence: 01-evidence.md §B.1-3.
2. Principle #3+#7 — ArticleEditor 重做: 删除全部 7 处 gradient、2 处 backdrop-filter blur、6 处大阴影、35+ 处圆角违规（16px/999px/50%），重写为与全局一致的 2px-radius 扁平化。Evidence: src/components/ArticleEditor/index.vue:471,482,483,501,507,514,533,540,547,566,569,572,590,593,595,600,617,630,635,643,651,664,665,670,681,685,697,713,714,717,722,728,739,740,745,746,759,763,769,791,796,802,813,816,822,827,834,836,848.
3. Principle #8 thorough — 可访问性与状态补全: 恢复 :focus-visible（删除 src/styles/element-override.scss:41 的 outline: none !important）；为 4 类 clickable div 加 role=button tabindex=0 @keydown.enter（stat-card src/views/content/article/index.vue:5,9,13,17、TagsView tag src/layouts/default/TagsView.vue:3-13、Header user-menu src/layouts/default/Header.vue:41-45、login captcha src/views/login/index.vue:64）；CrudTable 加 el-skeleton 替代 v-loading spinner；为 placeholder 提对比度至 ≥4.5:1（当前 2.45:1）；加 skip-link； layouts/default/* 加 <header>/<main>/<footer> landmark。
4. Principle #9 environmentally — Element Plus 真正按需: 删除 src/main.ts:3,4,12 的全量 import，让已配置好的 ElementPlusResolver（vite.config.ts:12-20）生效。预期首屏 JS 从 1,094 KB → ~250 KB。同时加 prefers-color-scheme 媒体查询（dark mode 跟系统），加 prefers-reduced-motion 门控 .dot-live 脉冲（src/views/dashboard/index.vue:137-139）和路由 transition（src/styles/transitions.scss:1-13）。Evidence: 01-evidence.md §D.1, §D.8.
5. Principle #6+#8 — 错误与文案收口: src/api/request.ts:110 不再吐 error.message，统一映射为用户可读文案；清理 src/hooks/useExport.ts:42 的"任务标识"内部术语；清理 src/views/settings/seo/index.vue:200 的 JSON.stringify(res) 调试残留；为 12 处 jargon 加 plain-language 替换或 tooltip（IndexNow Key src/views/settings/seo/index.vue:26、GSC 凭据 :43、Bing API Key :62、Phase 2 才会用 :45,63、slug src/views/content/keyword/index.vue:373,411、PAYLOAD src/views/seo/log.vue:16、死信 src/views/system/task_monitor/index.vue:88 和 src/views/system/monitor/index.vue:95、索引健康度 src/views/seo/dashboard.vue:56、质量闸 src/views/settings/seo/index.vue:85）；修 3 处 label-behavior 不符（AI 种子词 vs 手动采集 src/views/content/keyword/index.vue:42 vs :62；下线→candidate :445-454；404 返回首页指向 src/views/error/NotFound.vue:6 + src/router/static.ts:33）。

Out of scope for this refine pass:
- 不重写 CrudTable / useCrud / crud_router 后端 — 它们是 #2=3 的支柱
- 不改路由自动注册机制（router/dynamic.ts） — 同样支柱
- 不动 Element Plus 主色 #2563EB — 已是规范
- 不为登录页/dashboard 增加额外视觉装饰 — #5 unobtrusive 已达标
- 不引入新的 UI 库 / 不替换 Element Plus
- 不做 onboarding / guided tour / 营销页面 — admin 不面向首次用户营销

Deliverables for the plan:
- Per-fix: target files, exact change, verification step
- Token/spec changes consolidated in src/styles/variables.scss + theme.scss（单一事实源）
- Regression checklist for every "Keep" item above
- ArticleEditor redesign 视觉稿先行 — 必须大色块、纯平面、零阴影/渐变/模糊/位移，与项目 CLAUDE.md 视觉偏好一致
- a11y 验证步骤：键盘 Tab 走通主流程、axe-core 跑 dashboard + article 页 0 critical
- 构建验证：npm run build 后首屏 JS ≤ 300 KB raw

Anti-patterns to guard against (specific to REFINE):
- Adding new abstractions where a direct change suffices（不要再加一层 token alias，直接改值）
- Restyling areas that already scored 3（CrudTable 内部、useExport、router/dynamic.ts 保持不动）
- Scope creep into structural redesign（如发现需要换 UI 库或重写 CRUD 架构，立即停下重新评估为 REDESIGN）
- Letting fixes mutate principles outside the priority list（#1 innovative、#7 long-lasting 是被动受益，不要主动优化它们）
- 引入新的视觉装饰（新 gradient、新 shadow、新动画）— 用户明确禁止
- 把 jargon 换成另一组 jargon — 用日常中文，不用新造的术语
```
