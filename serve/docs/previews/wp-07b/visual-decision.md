# WP-07B 前置视觉决策 — Episode Detail 高保真预览

> 状态：`READY_FOR_VISUAL_CONFIRMATION`（仅视觉预览，未创建 WP-07B task/页面）
> 前置：WP-07A ACCEPTED（code `280afccc…`、head `b1000070`、manifest `881ab05c…`）
> 生成：2026-08-12

## 1. 页面单一目的

让操作员在 10 秒内回答：**这个新闻市场机会发生了什么、系统为什么作出当前判断、证据链是否完整、现在是否需要关注异常？**

阅读顺序固定：身份与当前结论 → 关键判断（Blind Forecast vs Market Reference）→ 为什么（Evidence/AI）→ 发生过程（统一时间线）→ 经济与执行结果 → 审计下钻。

## 2. 推荐 Token（唯一方案）

### 2.1 Palette 与 semantic role

| role | 值 | 用途 |
|---|---|---|
| canvas | `#F3F0E8` | 页面底 |
| surface | `#FFFDF8` | 分区/导航面 |
| ink | `#12151A` | 正文 |
| ink-muted | `#5F636B` | 次级文本/表头 |
| line | `#C9C5BA` | 1px 边框/分隔线 |
| primary | `#2757C7` | 主操作/链接/focus 环 |
| primary-block | `#173B86` | 主按钮/激活导航块 |
| success / success-soft | `#147A5B` / `#DCEFE7` | 通过/健康 |
| warning / warning-soft | `#A15C00` / `#F8E7C8` | 过期/待决 |
| danger / danger-soft | `#B42318` / `#F7DEDC` | 失败/错误 |
| info / info-soft | `#176B87` / `#DCECF1` | 信息/中性状态 |

状态一律**文本+图标双编码**（非仅颜色）；badge 全圆角仅限状态标签，分区为 4/8px 圆角。

### 2.2 Typography
- UI：`Inter, "Noto Sans SC", system-ui, sans-serif`
- 数字/哈希：`ui-monospace, "SFMono-Regular", monospace`
- h1 22px/700，h2 15px/700，正文 14px，表头 12px uppercase，mono 12.5px

### 2.3 Spacing / Density
- 基础 4px；核心节奏 8/12/16/24/32
- 控件高 36px；数据行基准 40px；触控目标 ≥44px（窄屏导航项）

### 2.4 Radius / Border / Focus
- 圆角 0/4/8px：常规控件 4px、分区最多 8px、状态标签全圆角（禁满页胶囊）
- 边框 1px；焦点环 2px；**禁止 shadow/gradient/glass/blur/highlight/lift/scale**

## 3. Layout 与 breakpoint

- 桌面 `1440`：左导航 224px + 主区；Gate 条带 8 列
- 平板 `1024`：导航 200px；Gate 4 列
- 移动 `390`：导航转顶部横向滚动的纯色条；主区单列；Gate 2 列；宽表格在 **section 内**横向滚动，页面级无横向溢出
- 五态（default/loading/empty/partial/denied/error）只切换可见层，不改变结构尺寸（无布局跳动）

## 4. 状态与边界值

- loading：骨架屏（不闪回旧数据）；empty：无事实面板；partial/stale：**附加**提示条，不替换正文；
  denied：权限面板（声明所需 perm）；error：失败面板 + 重试
- 真实长标题（Howey 判定题）、64 位 hash、`0.000138 pUSD` 金额、null（`disposition` 空态）、
  STALE quote、NOT_RUN gate、`SEC v. LBRY` 判例引用等边界
- 下钻链接（artifact/hash/trace/release）用真链接样式（`link` 类 + `↗`），静态值用 `plain` 类，不伪装成按钮

## 5. 浏览器 / 可访问性 / 真实验收（Playwright Chromium 149）

| 检查 | 结果 |
|---|---|
| desktop 1440×1000 overflow | 0 |
| tablet 1024×900 overflow | 0 |
| mobile 390×844 overflow | 0 |
| console / page error | **0 / 0** |
| failed request | 0（自包含无外网） |
| 键盘 focus 主要下钻控件（前 12） | OK（`focus-visible` 2px 环） |
| headings / landmark / lang | 13 / nav·main·nav / `zh-CN` |
| 200% zoom（mobile） | 无页面横向溢出 |
| 五态切换布局跳动 | 无（scrollHeight 稳定/仅附加层） |

## 6. 待用户确认

1. **采用此视觉方向**（palette/字体/间距/圆角/布局），或指出要改的具体 token/布局；
2. 若确认，下一步创建 WP-07B READY 合同并实现 14 菜单页 + 5 详情页（含本 Episode Detail 的页面级实现）。

## 7. 输出文件与 SHA-256

| 文件 | SHA-256 |
|---|---|
| `serve/docs/previews/wp-07b/episode-detail-preview.html` | `fcfe6cb86e35f4710bdbcdda5e278713ed4ed5a8e36ecc0c3eacffa0149392d7` |
| `serve/docs/previews/wp-07b/episode-detail-desktop.png` | `fbf4244d8e34c9ea002731a33038721dc19e2d68c2a501f39d60c4d83197dddd` |
| `serve/docs/previews/wp-07b/episode-detail-tablet.png` | `2318780a5b714940158cac2d2ef9ddafd59166b2bc385197fd69a29c303b8e91` |
| `serve/docs/previews/wp-07b/episode-detail-mobile.png` | `61d3c08c2a5116ba17076946a42b60d99f413d7b077a42818342cfd6fc16b968` |
| `serve/docs/previews/wp-07b/visual-decision.md` | `54fcbcbed39c330648db8de1f758b8c1abca5eed8f91d614d7b5952290f1eda0` |

> 浏览器：Chromium 149（Playwright）；截图命令见 `episode-detail-preview.html` 的渲染过程（三 viewport 全页截图，同一 HTML，无外网请求）；`git diff --check` 通过。

## 8. 未实施声明

- 未创建 `serve/docs/tasks/wp-07b-*.md`；
- 未更新 tasks/manifests README；
- 未实现任何菜单页/详情页/视觉组件；
- 未修改生产 API、数据库、权限、菜单或业务状态机。
