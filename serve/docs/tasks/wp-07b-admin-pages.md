# WP-07B — Admin 菜单页与详情页（14 列表 + 5 详情）

> 状态：**READY**
> 前置：`WP-07A` ACCEPTED；**视觉决策已确认**（`docs/previews/wp-07b/visual-decision.md`，2026-08-12 用户确认）；head=`b1000070`；manifest SHA=`881ab05c…eb1`
> 执行模型：DeepSeek V4 Flash
> 唯一完成交付：`serve/docs/manifests/wp-07b-admin-pages.md`
> 最后更新：2026-08-12 EDT

## 0. 快车道执行规则

1. 连续完成四个 Checkpoint：A 视觉 token 落地 + 菜单/路由基建 → B 14 列表页 → C 5 详情页 →
   D 浏览器验收/交互边界/manifest。只生成一份 completion manifest。
2. **视觉方向已冻结**（用户确认「就这样吧」）：严格平面、大块纯色、暖中性 canvas、蓝主色。
   任何页面不得违反 §3 视觉禁令；本 WP 不做视觉 token 的二次产品决策。
3. 页面只读：全部通过 WP-07A 的 `admin/src/api/v2/*` + `admin/src/queries/v2/*` 取数，
   不新增 mutation；不重算 Gate/PnL/edge/风险（展示后端返回值）。
4. 范围内 P0/P1 直接修复并复验，不拆 `-rN`。
5. 真实浏览器（Playwright）验收是本 WP 硬门槛：三 viewport、console error=0、无页面级横向溢出。
6. WP-07B 接受前不创建 WP-07C/WP-08。

## 1. 目标与用户价值

把 WP-07A 的只读 API + typed data layer 变成可现场演示的 Admin 页面，让操作员：
1. 用 14 个菜单页完成日常监控（系统健康、市场/组件/episode/decision/执行/模型/cost/配置/发布/评审/回放/完整性/artifact）；
2. 从任意列表/告警进入 5 个隐藏详情页做下钻（Market/Component/Episode/Decision/AI Invocation）；
3. 每个页面在 loading/empty/partial/error/denied 五种状态下都稳定、无布局跳动；
4. 键盘可操作、移动端可用、200% zoom 可用、console 无错误。

## 2. 已确认决策

### 2.1 视觉 token（冻结自 `docs/previews/wp-07b/visual-decision.md`）

```text
canvas #F3F0E8 · surface #FFFDF8 · ink #12151A · ink-muted #5F636B · line #C9C5BA
primary #2757C7 · primary-block #173B86
success #147A5B / success-soft #DCEFE7
warning #A15C00 / warning-soft #F8E7C8
danger #B42318 / danger-soft #F7DEDC
info #176B87 / info-soft #DCECF1
font-ui Inter,"Noto Sans SC",system-ui,sans-serif
font-mono ui-monospace,"SFMono-Regular",monospace
spacing 4px base; 8/12/16/24/32 rhythm; control 36px; row 40px; touch ≥44px
radius 0/4/8; control 4px; section ≤8px; status badge full-round
border 1px; focus ring 2px
```

**视觉禁令（不可违反）**：无 shadow/gradient/glass/blur/highlight/lift/scale/浮卡；
不用大量圆角卡片掩盖 IA；无 Apple 外观模仿、巨型标题或装饰性留白。

### 2.2 数据合同（WP-07A 已冻结）

- 页面只经 `admin/src/api/v2/*` + `admin/src/queries/v2/*` 取数；BIGINT/NUMERIC 为字符串；
  time 为 UTC ISO；hash 为 64-hex；列表统一 `CursorPage<T>`（keyset，无 OFFSET/COUNT）。
- 权限：列表页依赖 `v2:<domain>:view`；artifact 依赖 `v2:artifact:read`（AI artifact 另需
  `v2:ai:artifact`）。非超管无权限时页面显示 denied 态，不发请求。
- 关键状态由后端字段表达：episode `status/cognition_status`、decision `status/decision_class`、
  `selected_action_type`、gate `result`、AI `lifecycle_state` 等；页面用 StatusBadge 双编码
  （文本 + 图标/色块），**不重算**。

## 3. 页面范围（14 菜单 + 5 详情）

### 3.1 菜单页（type=1，挂对应 v2:*:view 权限）

| # | 菜单 label | path | template_path | 权限 |
|---|---|---|---|---|
| 1 | Dashboard | `/v2/dashboard` | `v2/dashboard/index` | v2:dashboard:view |
| 2 | Markets | `/v2/markets` | `v2/markets/index` | v2:markets:view |
| 3 | Components | `/v2/components` | `v2/components/index` | v2:components:view |
| 4 | Episodes | `/v2/episodes` | `v2/episodes/index` | v2:episodes:view |
| 5 | Decisions | `/v2/decisions` | `v2/decisions/index` | v2:decisions:view |
| 6 | Execution | `/v2/execution` | `v2/execution/index` | v2:execution:view |
| 7 | Models & AI | `/v2/models-ai` | `v2/models-ai/index` | v2:models:view |
| 8 | AI Invocations | `/v2/ai-invocations` | `v2/ai-invocations/index` | v2:ai:view |
| 9 | Costs | `/v2/costs` | `v2/costs/index` | v2:costs:view |
| 10 | Strategy Config | `/v2/config` | `v2/config/index` | v2:config:view |
| 11 | Releases | `/v2/releases` | `v2/releases/index` | v2:release:view |
| 12 | Evaluation | `/v2/evaluation` | `v2/evaluation/index` | v2:evaluation:view |
| 13 | Replay | `/v2/replay` | `v2/replay/index` | v2:replay:view |
| 14 | Integrity | `/v2/integrity` | `v2/integrity/index` | v2:integrity:view |

Artifacts 不作为独立菜单页（从下钻进入），但保留 `/v2/artifacts` 隐藏路由供 artifact 下钻。

### 3.2 详情页（隐藏路由，is_visible=false，不占侧边菜单）

| 详情页 | path | template_path |
|---|---|---|
| Market Detail | `/v2/markets/:id` | `v2/markets/detail` |
| Component Detail | `/v2/components/:id` | `v2/components/detail` |
| Episode Detail | `/v2/episodes/:id` | `v2/episodes/detail`（对应已确认高保真预览） |
| Decision Detail | `/v2/decisions/:id` | `v2/decisions/detail` |
| AI Invocation Detail | `/v2/ai-invocations/:id` | `v2/ai-invocations/detail` |

### 3.3 列表页统一行为

- PageShell + 面包屑 + 标题 + 主操作区（导出/刷新等只读动作）；
- Element Plus `el-table` 扁平覆写（沿用 element-override.scss）+ keyset 翻页
  （`next_cursor`/`has_more`，改变 limit 不改变 cursor/as_of）；
- filter 改变 → 清空 cursor/as_of（复用 WP-07A query key 语义）；
- 每页至少一个真实业务 filter（如 episodes.status、decisions.decision_class、ai.role）；
- loading 骨架屏、empty 空态、error 失败重试、denied 权限面板、partial/stale 附加提示——五种
  状态统一由 `usePageState` 组合，切换不引发布局跳动；
- 行内下钻链接（详情页）用真链接样式，静态值不伪装成按钮。

## 4. Checkpoint A — 视觉 token + 菜单/路由基建

### 4.1 精确生产文件

```text
serve/alembic/versions/b1000071_v2_0071_admin_page_menus.py   # 14 菜单页 seed + 详情页隐藏路由菜单 + 挂权限
serve/app/main.py                                             # 无改动（若菜单驱动自动生成则不加路由）
admin/src/styles/v2-tokens.scss                               # WP-07B 视觉 token（冻结 §2.1）
admin/src/styles/index.scss                                   # @import v2-tokens
admin/src/router/v2.ts                                        # 5 详情页隐藏路由 + artifacts 隐藏路由
admin/src/router/static.ts                                    # rootRoute.children 追加 v2 隐藏路由
admin/src/views/v2/_shared/{StatusBadge,PageHeader,KeyValueGrid,GateStrip,Timeline,DetailSection,EmptyState,ErrorState,DeniedState,SkeletonStrip,usePageState}.vue
```

### 4.2 菜单 seed（0071）

- 在 0070 不可见目录 `v2-admin` 下创建 14 个 type=1 菜单页（`path/template_path/is_visible=true`，
  挂 §3.1 对应 `v2:*:view` BUTTON 权限）+ 5 个 type=1 隐藏详情路由（`is_visible=false`，
  path 含 `:id`，不挂菜单可见性）+ Artifacts 隐藏路由。
- slug/perms 冲突但内容不全等 → migration fail；不自动授予普通角色（不写 role_menus）；
  超级管理员按 Base 规则绕过；不依赖固定 menu ID（parent_id 引用 0070 目录实际 id）。
- downgrade 前：存在 role_menus 绑定到 0071 菜单 → 整次拒绝；删 0071 菜单行，不动 0070 权限
  与 trading facts。
- 空库（无 Base menus）→ 菜单 seed 静默跳过（同 0070 门控）；真实部署 Base init.sql 先建。

## 5. Checkpoint B — 14 列表页

### 5.1 精确生产文件

```text
admin/src/views/v2/dashboard/index.vue
admin/src/views/v2/markets/index.vue
admin/src/views/v2/components/index.vue
admin/src/views/v2/episodes/index.vue
admin/src/views/v2/decisions/index.vue
admin/src/views/v2/execution/index.vue        # tabs：Intents/Orders/Positions/Ledger
admin/src/views/v2/models-ai/index.vue
admin/src/views/v2/ai-invocations/index.vue
admin/src/views/v2/costs/index.vue
admin/src/views/v2/config/index.vue
admin/src/views/v2/releases/index.vue
admin/src/views/v2/evaluation/index.vue       # tabs：Labels/Metrics/Promotions
admin/src/views/v2/replay/index.vue
admin/src/views/v2/integrity/index.vue        # tabs：Alerts/Workflows/External Calls
```

每页：PageHeader + 状态条（五态）+ filter 行 + keyset 表格 + 下钻链接 + 翻页（has_more/next_cursor）。

## 6. Checkpoint C — 5 详情页

### 6.1 精确生产文件

```text
admin/src/views/v2/markets/detail.vue          # market + snapshot/specs/current/cohort 链
admin/src/views/v2/components/detail.vue       # component + versions/member_contracts
admin/src/views/v2/episodes/detail.vue         # 高保真预览落地：identity/状态/Blind vs Market/
                                                # Gate 条带/Evidence/AI/Decision+action/时间线/审计
admin/src/views/v2/decisions/detail.vue        # decision + quote/underwriting/action_sets/intents
admin/src/views/v2/ai-invocations/detail.vue   # invocation + binding/tool/validator/downstream（不内联 raw）
```

详情页遵循 §2.1 token 与已确认 Episode Detail 预览的阅读顺序与结构；下钻链接（artifact/hash/
release/trace）全部指向真实路由或 artifact 元数据面板。

## 7. Checkpoint D — 浏览器验收、交互边界、manifest

### 7.1 精确测试/交付文件

```text
admin/src/views/v2/**                       # 全部页面
admin/src/styles/v2-tokens.scss
admin/src/router/v2.ts
serve/alembic/versions/b1000071_v2_0071_admin_page_menus.py
serve/tests/trading/integration/test_v2_0071_admin_page_menus_migration.py
serve/docs/previews/wp-07b/                # 已确认视觉决策（冻结，不改）
serve/docs/manifests/wp-07b-admin-pages.md
serve/docs/manifests/README.md
serve/docs/tasks/README.md
```

### 7.2 必测事实

1. 14 菜单页 + 5 详情页路由全部可达（动态菜单 + 隐藏路由）；
2. 每页五态（loading/empty/partial/error/denied）切换无布局跳动、console error=0；
3. keyset 翻页跨页 as_of/filter_hash 一致，改变 limit 不改变 snapshot；filter 改变清 cursor；
4. 详情页链 ID 全等，页面不重算 Gate/PnL/edge/风险；
5. Episode Detail 与已确认高保真预览视觉一致（token/结构/阅读顺序）；
6. 三 viewport（1440/1024/390）无页面级横向溢出；200% zoom 可用；键盘 focus 顺序有效；
7. 0071 空库/Base 升级、slug 冲突 fail、downgrade preflight（role_menus 绑定拒绝）、
   offline SQL、ORM drift 全过；
8. WP-07A 前后端回归不变；真实 outbound/network/chain/money=0。

### 7.3 浏览器验收命令

```bash
cd admin && npm run lint && npm run build && npm run test -- --run
# Playwright：三 viewport 截图 + console/page error=0 + horizontal overflow=0 + 键盘走查
python3 tests/e2e/shot_v2_pages.py   # （脚本见交付；截图落 serve/docs/previews/wp-07b/final/）
```

## 8. 验收命令

```bash
cd /code/pollymarket/v2/serve
.venv/bin/alembic upgrade b1000071 --sql > /tmp/wp07b.sql
V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q \
  tests/trading/integration/test_v2_0071_admin_page_menus_migration.py
cd /code/pollymarket/v2/admin && npm run lint && npm run build && npm run test -- --run
# 浏览器验收（§7.3）
cd /code/pollymarket/v2/serve
V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q tests/trading
V2_TEST_ADMIN_DATABASE_URL='postgresql+psycopg:///postgres' .venv/bin/pytest -q
.venv/bin/alembic heads
git diff --check
```

必须 0 skip/0 fail，唯一 Alembic head=`b1000071`；offline SQL secret marker=0；
console/page error=0；真实 outbound/network/chain/money=0。

## 9. Completion manifest 合同

只创建：

```text
serve/docs/manifests/wp-07b-admin-pages.md
```

至少记录：状态与实现 commit、精确 changed files、0071 菜单 seed/权限/downgrade、
14 菜单页 + 5 详情页矩阵、视觉 token 落地、五态/keyset/详情链/浏览器验收证据、
perf/console/accessibility 结果、full regression、blocker/non-goal/rollback。

完成后按本 task 冻结口径计算唯一 self SHA：删除且只删除最后一行

```text
COMPLETION_MANIFEST_SHA256: <64 lowercase hex>
```

（含行尾 LF）后 SHA-256；将同一值同步两个 README，再独立复验。

## 10. Blocker、非目标与回滚

### Blocker

- WP-07A accepted head/manifest 与本任务前置不一致；
- 视觉决策未确认（已确认，冻结于 `docs/previews/wp-07b/visual-decision.md`）；
- 页面必须调用真实 provider/chain/money 或新增 mutation；
- 菜单 seed 与既有 slug/perms 冲突；
- 页面无法在真实浏览器满足 console=0 / 无横向溢出。

### 非目标

- 不新增后端 API/DB/权限（只读复用 WP-07A）；
- 不实现 config draft/publish、release rollback、kill、label adjudicate、replay create/cancel；
- 不做交易/回放/AI/rebuild/链操作；不改 V1；不建第二套事实表/账本；
- 不接真实 RPC/CLOB/Relayer/资金；不做 WP-07C/WP-08。

### 回滚

- 0071 downgrade 前先撤销 V2 role bindings（存在绑定时拒绝）；删 0071 菜单行不动 0070 权限；
- 前端 `views/v2/**`、`v2-tokens.scss`、`router/v2.ts` 可独立 revert；
- 不删除 artifact、ledger、projection、已确认视觉决策或 accepted manifest。

## 11. 交接

1. 全部 Checkpoint 完成后，创建唯一 manifest 并把两个 README 的 WP-07B 标为 `DONE（待审）`。
2. 不自行写 ACCEPTED，不创建 WP-07C。
3. 用户回复「完成」后，审查者读取 task/manifest/Git，复跑页面路由/五态/keyset/详情链/
   浏览器验收/0071 迁移/perf；范围内 P0/P1 直接修复。
4. WP-07B 审查接受并冻结 manifest 后，进入 WP-07C/WP-08 规划。
