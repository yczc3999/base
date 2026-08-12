# WP-07B 前置视觉决策 — DSF 执行提示词

> 状态：`READY_FOR_VISUAL_PREVIEW`
> 性质：这是 WP-07B 的**前置产品视觉确认**，不是 WP-07B 实施任务合同。
> 前置：WP-07A 已 ACCEPTED；code=`280afccc0adc8695b6a2508f27557c737571db16`；head=`b1000070`；manifest SHA=`881ab05c448fc6b345d0df97738e756a50bd6af2064cefc6c3968b72fff9feb1`。
> 目标：用一张可在真实浏览器检查的 Episode Detail 高保真预览，让用户一次性确认产品色板、语义颜色、字体、密度、间距和圆角，再创建 WP-07B 实施合同。
> 最后更新：2026-08-12 EDT

## 直接执行指令

读取并遵守：

1. 仓库根 `AGENTS.md`；
2. `serve/docs/tasks/wp-07a-admin-read-api-typed-data-layer.md`；
3. `serve/docs/manifests/wp-07a-admin-read-api-typed-data-layer.md`；
4. `serve/docs/polymarket-v2-platform-design.md` 的 Admin 信息架构；
5. `serve/docs/v2-implementation-contract.md` §9、§10；
6. `admin/src/api/v2/types.ts`、`admin/src/api/v2/episodes.ts`、`admin/src/api/v2/decisions.ts`、`admin/src/api/v2/execution.ts` 中已接受的数据合同。

只完成本提示词定义的视觉决策预览。**不要创建 WP-07B task，不要批量实现页面，不要修改生产 API、数据库、权限、菜单或业务状态机。**

## 1. 页面唯一目的

Episode Detail 必须让操作员在 10 秒内回答：

> 这个新闻市场机会发生了什么、系统为什么作出当前判断、证据链是否完整、现在是否需要关注异常？

页面阅读顺序固定为：

1. **身份与当前结论**：episode key、市场问题、状态、decision class、当前 disposition、cutoff；
2. **关键判断**：blind forecast、market-relative decision、置信/一致性、Gate 结果；
3. **为什么**：证据摘要、信息快照、模型/validator 状态；
4. **发生过程**：统一时间线；
5. **经济与执行结果**：action/intent、shadow execution、ledger/system-net 状态；
6. **审计下钻**：artifact/release/hash/trace 链接。

不要把每个字段都装进卡片。优先用清晰分区、表格、时间线、分隔线和大块纯色表达层级。

## 2. 必须提交的一套推荐视觉方案

只提交**一个明确推荐方案**，不要给用户三个模糊主题自己拼。

### 2.1 推荐 token 起点

允许在浏览器审查后微调，但最终报告必须给出精确值：

- 字体：`Inter, "Noto Sans SC", system-ui, sans-serif`；数字/哈希：`ui-monospace, "SFMono-Regular", monospace`；
- 基础间距：4px；核心节奏：8 / 12 / 16 / 24 / 32；
- 控件高度：36px；数据行基准：40px；触控目标不小于 44px；
- 圆角：0 / 4 / 8px；常规控件 4px、重要分区最多 8px；状态标签可用全圆角，但禁止满页胶囊；
- 画布：暖中性浅底；正文为近黑；主色用克制的高辨识蓝；success/warning/danger/info 各只有一个基础色与一个浅背景色；
- 边框 1px；焦点环 2px；禁止用阴影表达层级。

推荐初始色值：

```text
canvas          #F3F0E8
surface          #FFFDF8
ink              #12151A
ink-muted        #5F636B
line             #C9C5BA
primary          #2757C7
primary-block    #173B86
success          #147A5B
success-soft     #DCEFE7
warning          #A15C00
warning-soft     #F8E7C8
danger           #B42318
danger-soft      #F7DEDC
info             #176B87
info-soft        #DCECF1
```

颜色必须通过 WCAG 对比度检查，且状态不能只靠颜色区分。

### 2.2 用户已冻结的视觉禁令

必须是大块纯色、严格平面。禁止：

- shadow；
- gradient；
- glass / blur；
- highlight 光泽；
- raised / inset surface；
- floating card；
- hover lift、scale 或 3D 位移；
- 用大量圆角卡片掩盖信息架构；
- Apple 外观模仿、巨型标题或装饰性留白。

## 3. 高保真 Episode Detail 内容

使用 `admin/src/api/v2/types.ts` 的真实字段形状制作 deterministic local fixture；不得出现 secret、raw prompt、raw response、auth header 或生产账户信息。

预览至少包含：

- 左侧产品导航：Dashboard、Markets、Episodes、Decisions、Execution、Models & AI、Evaluation、Integrity；
- 面包屑与 Episode identity；
- 当前状态区：`COMPLETE`、`HOLD_TO_RESOLUTION`、`RISK_REVIEW` 等有文本与图标双编码；
- 市场问题、截止时间、resolution 状态；
- Blind Forecast 与 Market Reference 的并列但不混淆展示；
- G0–G7B Gate 条带：PASS/FAIL/NOT_RUN 均有明确文本；
- Evidence 摘要：来源、freshness、artifact lineage；
- AI invocation 摘要：provider/model、tool/validator exact counts、accepted/failed 状态，不内联 raw 内容；
- Decision/action/intent 与 shadow fill / ledger / system-net 分层；
- 合并时间线：submission、information snapshot、Gate、decision、execution；
- 可下钻链接必须看起来可交互，静态值不得伪装成按钮；
- 真实长标题、64 位 hash、大金额/小数、null、stale、partial 等边界值。

页面默认态之外，必须在同一预览文档中展示或可切换查看：loading、empty、partial/stale、permission denied、API error 五种关键状态；状态切换不得引发布局跳动。

## 4. 响应式与可访问性

至少真实渲染并截图：

- Desktop：1440×1000；
- Small laptop/tablet：1024×900；
- Mobile：390×844。

要求：

- Desktop 不能是卡片拼贴墙；Mobile 不能只是机械地把所有列纵向堆叠；
- 宽表在窄屏采用字段优先级/分组展开策略，不允许页面级横向溢出；
- 键盘 focus 顺序、focus-visible、语义 heading、landmark、按钮 accessible name 全部有效；
- 200% zoom 可用；文字与控件不截断；prefers-reduced-motion 下无非必要动画；
- hover/focus/pressed/selected/loading/error 状态不改变边框宽度或造成位移。

## 5. 精确输出范围

仅允许创建：

```text
serve/docs/previews/wp-07b/episode-detail-preview.html
serve/docs/previews/wp-07b/episode-detail-desktop.png
serve/docs/previews/wp-07b/episode-detail-tablet.png
serve/docs/previews/wp-07b/episode-detail-mobile.png
serve/docs/previews/wp-07b/visual-decision.md
```

HTML 必须是可离线打开的自包含静态预览，不依赖公网字体/CDN，不连接 backend，不修改 `admin/src/**`。

`visual-decision.md` 必须精确记录：

- 页面单一目的；
- palette 与每个 semantic role；
- typography；
- spacing/density；
- radius/border/focus；
- layout 与 breakpoint；
- loading/empty/partial/error/permission 状态；
- accessibility 检查；
- 哪些设计决定等待用户确认；
- 五个输出文件 SHA-256；
- 浏览器、viewport、截图命令与 console error count。

## 6. 浏览器验收

使用真实 Chromium/Playwright 渲染，而不是只看源码或 build：

1. 三个 viewport 截图必须来自同一 HTML；
2. console error=0、page error=0、failed request=0；
3. 页面级 horizontal overflow=0；
4. 截图逐一人工检查文字、间距、对齐、层级、边界状态；
5. 用键盘走完主要下钻控件；
6. 运行可用的 accessibility scan，并记录结果；
7. `git diff --check` 通过。

任何地方若只是“能看”，但不适合现场大屏演示，继续改到结构、视觉和文字都成立。

## 7. 交付格式

完成后只汇报：

1. 五个输出路径；
2. 三张截图预览；
3. 推荐 token 表；
4. 浏览器/可访问性/overflow/console 的真实结果；
5. 明确询问用户：`采用此视觉方向` 或指出要改的具体 token/布局。

用户明确确认前：

- 不创建 `serve/docs/tasks/wp-07b-*.md`；
- 不更新 tasks/manifests README 为 READY；
- 不实现 14 菜单页或 5 详情页；
- 不宣称 WP-07B 开始或完成。
