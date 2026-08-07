# tofix.md — 待修 / 已修问题台账

> 记录 base 平台及其衍生项目(如 pillarwise-ops)中发现的通用问题。按时间追加, 标记状态。
> 约定: 🔴 严重 / 🟡 中等 / 🟢 已修(留档)。

---

## 🟢 [已修 2026-08-06] vue-router 根路径/未知路径 未登录时渲染 404 而非跳登录

**来源**: pillarwise-ops 克隆 base 后台时发现并修复。

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

### 验证方法(headless)
用 puppeteer-core + 系统 Chrome:
- 未登录访问 `/` → 应 `#/login?redirect=/dashboard`
- 注入 token 后访问 `/` → 应自动跳 `#/dashboard`

### 参考
- vue-router 4: redirect 与 beforeEnter 的时序关系。
- 修复提交: pillarwise-ops `38670e1`。

---

<!-- 后续问题追加在下面 -->
