# tofix.md — 待修 / 已修问题台账

> 记录 base 平台及其衍生项目(如 pillarwise-ops)中发现的通用问题。按时间追加, 标记状态。
> 约定: 🔴 严重 / 🟡 中等 / 🟢 已修(留档)。

---

## 🔴 [待修 · 2026-08-15 · Polymarket V2] universe frame 持续溢出，市场事实为 0

**目标与用户价值**：恢复真实市场发现，使 AI、决策和组合降险获得可审计输入。

**真实状态**：常驻进程仍在运行；数据库快照为 259 frames（258 `FAILED`、1 `OPEN`）、
51,158 frame pages，全部 endpoint=`events_open`；`pm_markets=0`，全部下游事实为 0。

**根因链**：`events_open` 位于四条 endpoint 的首位，200 页上限触发
`frame_page_overflow`，执行未进入 `markets_open`；pipeline `_sense()` 对失败结果仍返回
`ok=True`，形成存活但不推进的假健康。

**精确范围**：见 `serve/docs/tasks/wp-07c-resident-runtime.md` Checkpoint C。

**验收证据**：冷启动与断点恢复各得到完整 frame；四条 cursor 链终止；非零 market；
`confirmed markets = memberships = R0 dispositions`；失败状态进入 health/alert；artifact 重放 hash 一致。

**风险/回滚**：保留所有 raw artifact/failed frame；cursor 策略变化生成新版本，不回写旧 frame。

**最后更新**：2026-08-15T00:37:50-04:00

---

## 🔴 [待修 · 2026-08-15 · Polymarket V2] AI/决策/执行 runtime 只有注册，没有闭环

**真实状态**：`PipelinePolicy.ai_enabled=False`，pipeline 注入 `cognition_runtime=None`，
Stage 2～4 固定返回 `ai_gated`；cognition/evaluation/execution/reconciliation/replay spec
均返回 idle runner。`ai_invocations/forecast_submissions/trade_decisions/action_sets/executions/
metric_runs` 全为 0。

**处理决定**：Stage 0 关闭后，在 WP-07C Checkpoint D 接入模型 gateway、实际推进循环和
shadow-only action/execution/evaluation/replay；dry-run 注册、类实例化或空表不算完成。

**验收证据**：至少一条完整 shadow 因果链；blind/reveal 隔离、G7A/G7B、ledger 平衡、
重复 effect=0、固定 artifact replay hash 相同。

**依赖/blocker**：模型 gateway、role binding、服务端 provider credential；真 PG 测试管理员 URL。

**非目标**：不授予 canary/live permission，不发送真实订单。

**最后更新**：2026-08-15T00:37:50-04:00

---

## 🟡 [待修 · 2026-08-15 · Polymarket V2] 任务、manifest 与真实状态漂移

**症状**：任务指针停在 WP-07B；manifest 索引把 WP-07B/WP-07C 标成 DONE；WP-07C 文件只记录
Checkpoint A，却被当作最终 completion manifest；当前工作树另有 53 tracked modified + 6 untracked。

**本次更新**：任务指针改为 WP-07C IN_PROGRESS；WP-07B 标为
`REMEDIATION_REQUIRED`；WP-07C Checkpoint A 降回历史证据，最终 manifest 标为尚未生成；
README、task、manifest、tofix 四套账本统一到 2026-08-15 快照。

**剩余验收**：每次状态变更重新记录 release/git/db revision、真实命令和计数；WP 完成前只生成
一份最终 manifest，禁止 checkpoint 冒充里程碑完成。

**最后更新**：2026-08-15T00:37:50-04:00

---

## 🟡 [待修 · 2026-08-15 · Polymarket V2] Admin 浏览器硬门未关闭

**证据**：Chromium 1440/1024/390 渲染无页面级横向 overflow、console error=0；但 390px
总览表格逐字换行、信息不可读。业务页仅使用 mock API 完成三视口检查，尚无真实后端、权限、
数据的 14 列表页 + 5 详情页全链 E2E。登录页仍显示通用 `Plymkt/Base Platform` 文案。

**验收证据**：390px 与 200% zoom 可读；键盘顺序/focus 可见；五态与详情链在真实后端通过；
三视口 console/page error=0；页面级 overflow=0。

**非目标**：不通过隐藏列、缩小到不可读字号或只检查 `scrollWidth` 关闭问题。

**最后更新**：2026-08-15T00:37:50-04:00

---

## 🟡 [待修 · 2026-08-15 · Polymarket V2] 当前 release 验收证据不完整

**证据**：offline backend `1699 passed / 368 skipped`；runtime target `19 passed / 3 skipped`；
frontend `40 passed`、lint 0 error/3 warning、build pass。真 PostgreSQL suite 因测试用户无
`CREATE DATABASE` 权限未复验。`alembic current=head=b1000075`，但 `alembic check` 因动态分区、
FK/索引差异返回 255，尚无 partition-aware drift 通过证据。

**验收证据**：使用专用测试管理员创建/销毁临时库；迁移 upgrade/downgrade/upgrade；全量真 PG；
为动态分区设置明确的 Alembic include/exclude 合同，使真实 model drift 与预期分区对象可区分。

**风险/回滚**：不得根据 autogenerate 输出直接删除生产分区、外键或索引。

**最后更新**：2026-08-15T00:37:50-04:00

---

## 🟢 [已修 2026-08-07 · base] vue-router 根路径/未知路径 未登录时渲染 404 而非跳登录

**来源**: pillarwise-ops 克隆 base 后台时发现并修复; 2026-08-07 在 base 复现并应用同一修法。

### 症状
- 未登录访问后台根路径 `/` → 显示"404 页面不存在", 而不是跳到登录页。
- 登录后跳转 `/` → 再次 404, 不能直达仪表盘。

### 根因
vue-router 的 `redirect` 在**解析阶段**就完成重定向, **跳过目标路由的 `beforeEnter`**:
1. 未登录访问 `/` → `rootRoute.redirect: '/dashboard'`(动态路由, 未登录时未注册)
2. 找不到 `/dashboard` → 落到 catch-all `/:pathMatch(.*)*`
3. catch-all 的 `redirect: '/404'` → 同样跳过守卫
4. `/404` 在 `WHITE_LIST` 中, `beforeEach` 直接放行 → 渲染 404

未登录的鉴权守卫从未被触发。

### 修复(正确做法)
**catch-all 不能用 `redirect`, 要在守卫内跳转**:

```ts
// router/static.ts
{
  path: '/:pathMatch(.*)*',
  name: 'CatchAll',
  component: () => import('@/views/error/NotFound.vue'),
  meta: { title: '404', layout: 'blank' },
  beforeEnter: (to, _from, next) => {
    // redirect 会在解析阶段跳过 beforeEnter, 导致未登录绕过守卫直接渲染 404。
    // 在守卫内跳转: 未登录 → 登录页; 已登录 → 渲染 404。
    if (!isLoggedIn()) return next(`/login?redirect=${to.fullPath}`)
    return next()
  },
}
```

注意: 不要试图给 `rootRoute`(path: `/`) 或 `redirect` 路由加 beforeEnter, 它们不生效。

### base 落地
- 文件: `admin/src/router/static.ts`
- 提交: 待提交
- 验证: `npm run build` PASS（1.19s）

### 验证方法(headless)
用 puppeteer-core + 系统 Chrome:
- 未登录访问 `/` → 应 `#/login?redirect=/dashboard`
- 注入 token 后访问 `/` → 应自动跳 `#/dashboard`

### 参考
- vue-router 4: redirect 与 beforeEnter 的时序关系。
- 修复提交: pillarwise-ops `38670e1`。

---

## 🔴 [已修 2026-08-07] messages 表 schema 漂移 → dashboard 数据全空

**症状**: dashboard 四个统计卡 + 最近操作全显示 `—`。接口返回 HTTP 200 但前端静默全空。

### 根因
`messages` 表被某次**非受控手改**改成了多接收者事件化设计（`recipient_id` + `recipient_type` + event/biz/payload/idempotency 等扩展列），但:
- schema_migrations 无任何迁移改过它（init.sql 与迁移 013 均为 `user_id`）
- 整个 serve 后端 **0 处代码**使用 recipient 列
- 模型 `app/models/message.py` 用 `user_id`
- 表内 **0 行数据**

`dashboard_stats` 查 `Message.user_id` → SQL 生成 `messages.user_id` → 列不存在 → `UndefinedColumn` → `main.py` 全局 `exception_handler(Exception)` 转成 **HTTP 200 + {code:500}** → 前端 `showError:false` 静默吞掉 → 显示空。

### 修复
- 迁移 `030_messages_rebuild.sql`: DROP 漂移表 + 按 init.sql/迁移 013 重建 `user_id` 结构
- 原 recipient 结构备份: `databases/backups/messages_recipient_schema_20260707.sql`
- 验证: dashboard_stats 核心查询全通过（admin=2, month_operations=2507）

### 教训
**任何改表都必须走迁移 + 同步模型 + 同步代码**。手工改表是漂移根源。若未来确需多接收者消息（admin/customer/parttime），走正式迁移，勿再手改。

---

<!-- 后续问题追加在下面 -->
