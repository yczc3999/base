# tofix.md — 待修 / 已修问题台账

> 记录 base 平台及其衍生项目(如 pillarwise-ops)中发现的通用问题。按时间追加, 标记状态。
> 约定: 🔴 严重 / 🟡 中等 / 🟢 已修(留档)。

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
