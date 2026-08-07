# 技术雷达与激进升级路线（2026-08-07）

> 两次审计（前端页面约束 + 后端 Python 技术栈）合并结果。
> 目标：base 面向**无穷无尽的平台克隆**，需要的不只是加功能，而是**把约束从"建议"变成"结构强制"**，并引入激进新技术提升性能/稳定/速度。
> 状态约定：🟢 已落地 / 🟡 建议做 / 🔴 强推荐。
>
> **2026-08-07 更新：全部 11 项已落地（P0/P1/P2/P3 全绿）。后端 211 测试通过，前端 build PASS。落地清单见第五节。**

---

## ⭐ 落地总结（2026-08-07）

**后端 6 项全部落地**：ORJSON 序列化（全站提速 3-10x）、Alembic 迁移（永不漂移，baseline `cdabba1e3903`）、多 worker + lifespan 预热 + `/health/ready|live` 探针、SQL 批量 `selectinload` + N+1 消除（menu/role 批量删除修复）、Pydantic `response_model`（`app/schemas/base.py`，getList/getDetail 通用响应模型）。

**前端 5 项全部落地**：TanStack Query（monitor/task_monitor/dashboard 轮询改 `refetchInterval` 静默刷新，不再闪烁）、PageShell 页面脚手架（monitor/dashboard 已收敛）、SchemaCrudPage 终极形态（role 页已转 JSON 驱动）、`check:crud` build 时扫描（27 页 9 用 CrudTable）、ESLint + AppErrorBoundary + StatCard + `/docs` 组件文档页。

**验证**：`pytest` 211 passed · `npm run build` ✓ 1.30s · `/health/live` `/health/ready` 200。

---

## 一、现状快照（审计取证）

---

## 一、现状快照（审计取证）

### 前端 `admin/`
| 项 | 现状 | 缺口 |
|---|---|---|
| 页面 | 35 个 `.vue`，仅 11 个走 CrudTable | **24 个手写页绕过约束** |
| 工具 | `vue-tsc -b` 类型检查，strict 开 | **无 ESLint / 无 test**；`noUnusedLocals:false` |
| 数据层 | 每页 `onMounted` 手写 get + loading + error | **无请求缓存层**；2 页手写 `setInterval` 轮询（闪烁根因）|
| 分包 | wangeditor 797KB / menu 659KB 懒加载 | 无 hover 预取，无 manualChunks 拆 vendor |
| 已做 | CrudTable / confirmDialog / GaugeBar / CountUp / Sidebar+TagsView 统一图标 | 缺 PageShell / StatCard 收敛手写页 |

### 后端 `serve/`
| 项 | 现状 | 版本/深度 |
|---|---|---|
| 语言 | Python 3.12 | ✅ 很新 |
| 框架 | FastAPI 0.141 + SQLAlchemy 2.0 async + asyncpg | ✅ 全异步 |
| 连接池 | `pool_size` + `max_overflow` 配置 | ✅ |
| Redis | `redis.asyncio` | ✅ |
| 序列化 | 标准库 `json.dumps` | ❌ 无 orjson（慢 3-10x）|
| 迁移 | 手写 `migrations/*.sql` + 自定义 migrate.py | ❌ 无 Alembic |
| 任务 | 纯 asyncio + Redis 自研队列（零 Celery） | ✅ 符合哲学 |
| 测试 | 20 个 pytest | ✅ 但没进 CI |
| 运行时 | 单 uvicorn 进程 | ❌ 无多 worker / 无启动预热 |

**已踩过的坑（真实教训）**：
1. `messages` 表手工改表 → schema 漂移 → dashboard 数据全空（`UndefinedColumn` 被全局异常处理器吞成 HTTP 200 + code 500）
2. 定时轮询 `v-loading` 每 5s 弹遮罩 → 整页闪烁
3. 全局异常处理器把异常转 HTTP 200 → 前端 `showError:false` 静默吞掉 → 排查困难

---

## 二、前端新技术（激进度 × 收益排序）

### 🥇 F1. TanStack Query（@tanstack/vue-query）— 数据层质变
**🔴 强推荐**。现痛点全在数据层：
- 定时刷新闪烁（refetch 时整页 loading）
- 跨页面无缓存（切回列表又重新请求）
- 无失效机制（改一条数据，列表不知道要刷新）

**收益**：`refetchInterval` 后台静默轮询不闪屏 + 缓存去重 + mutation 后 `invalidateQueries` 自动刷新 + 乐观更新回滚 + 指数退避重试。
**落地**：替换 `views/*` 的 `onMounted` 手写请求；monitor/task_monitor 的 `setInterval` 全部换掉。

### 🥇 F2. PageShell 页面脚手架 — 页面约束收敛
**🔴 强推荐**。dashboard / monitor / task_monitor / seo / login 全手写 div 拼结构，样式悄悄漂移。
```vue
<PageShell title="系统监控" sub-title="实时资源" :actions="[...]">
  <!-- 内容区：空态/loading 由 shell 统一处理 -->
</PageShell>
```
**收益**：手写页只能往 PageShell 填内容，页头/空态/loading 样式零自由度。

### 🥇 F3. StatCard 统计卡组件 — 已复制 3 次
**🟡 建议**。dashboard / article / keyword 各自手写 stat-card，CSS 三份。抽 `<StatCard icon value label accent @click>`。

### 🥈 F4. CrudTable 升级为「列表页唯一入口」
**🟡 建议**。用 `import.meta.glob` 在 build 时扫描，检测列表页没走 CrudTable 就告警。约束从"记得用"→"不用就报错"。

### 🥈 F5. Schema 驱动（终极形态）
**🟡 建议**。一个页面 = 一份 JSON `{ statCards, filters, columns, batchActions, export }`，模板零代码。CrudTable 是半程，补上 statCards/filters 区即走完。

### 🥉 F6. 路由 hover 预取 + 分包
**🟡 建议**。菜单 hover 时 `import()` 预取下一 chunk；`manualChunks` 拆 wangeditor/vendor；加 `vite-bundle-analyzer`。

### 🥉 F7. 表格虚拟滚动 + v-memo
**🟢 数据爆炸时再上**。ElTable 上万行卡；`v-memo` 高频更新列减重渲。

### 🥉 F8. ESLint + TS noUnusedLocals + 错误边界
**🟡 建议**。目前完全无 ESLint（最刺眼）。`eslint-plugin-vue` + `noUnusedLocals:true` + `onErrorCaptured` 错误边界（崩了不白屏，显示降级重试）。

### 🥉 F9. 组件文档页 `/docs`
**🟢 新平台团队照抄**。把 tokens / PageShell / StatCard / CrudTable / confirmDialog 用法列出来。

---

## 三、后端新技术（激进度 × 收益排序）

### 🥇 B1. ORJSON 高速序列化 — 性价比之王
**🔴 强推荐**。FastAPI 默认 `json.dumps` 慢 3-10x。加 **orjson**，一条配置全局提速：
```python
from fastapi.responses import ORJSONResponse
app = FastAPI(default_response_class=ORJSONResponse)
```
**收益**：对大量 JSON 返回的 CRUD 系统是纯赚，API 响应时间直接砍半。

### 🥇 B2. Alembic 迁移 + 版本化 — 堵死漂移坑
**🔴 强推荐**。现状手写 SQL 迁移，**30 分钟前刚因"手写改表→schema 漂移→dashboard 全空"踩坑**。Alembic `autogenerate` 从模型生成迁移，模型改→迁移自动生成→绝不漂移。
**注意**：Alembic 只生成 schema，不迁移数据——业务数据迁移仍手写。

### 🥇 B3. 多 worker 并发 + 优雅关机 + 启动预热
**🟡 建议**。单 uvicorn → `--workers 4`（DB 池共享）+ `--timeout-graceful-shutdown` 优雅关机 + `lifespan` 启动预热 Redis/DB 连接（冷启动提速）+ 健康探针 `/health/ready` `/health/live`。

### 🥈 B4. SQL 批量优化 + N+1 消除
**🟡 建议**。27 处 `.all()` 全量加载。BaseLogic 加批量加载（`selectinload`）+ 分页游标 + `selectin` 批量。数据爆炸时列表页不卡。

### 🥈 B5. 健康端点 + Prometheus 指标
**🟢 生产时上**。`/health/ready`（依赖就绪）+ `/health/live`（进程存活）+ Prometheus 导出。K8s/探针/监控用。

### 🥉 B6. Pydantic v2 强类型 response_model
**🟡 建议**。部分接口手写 dict 返回。全部走 Pydantic response_model → 编译期校验、自动文档、类型安全。

---

## 四、行动优先级（落地顺序）

| 优先级 | 项 | 收益 | 成本 |
|---|---|---|---|
| **P0** | **B1 ORJSON** | 全站响应提速 3-10x | 一行配置 |
| **P0** | **B2 Alembic** | 永不 schema 漂移 | 中（初始迁移生成）|
| **P1** | **F1 TanStack Query** | 解决闪烁+缓存，数据层质变 | 中（替换手写请求）|
| **P1** | **F2 PageShell + F3 StatCard** | 页面约束收敛 | 低 |
| **P2** | **B3 多 worker + 预热 + 探针** | 生产级稳定 | 低 |
| **P2** | **F8 ESLint + 错误边界** | 工程规范防漂移 | 中 |
| **P3** | **F5 Schema 驱动** | 终极形态 | 高（架构改造）|
| **P3** | **B4 SQL 批量 + B6 Pydantic 全量** | 数据爆炸 + 类型安全 | 中高 |

**一句话**：现在有组件（CrudTable/confirmDialog）没系统。P0 两件（ORJSON + Alembic）先落地——前者一行配置全站提速，后者堵死刚踩的漂移坑。之后 P1（TanStack Query + PageShell）让数据层质变 + 页面收敛。

---

## 五、已落地清单（本会话累计）

### 技术雷达落地（2026-08-07）

| 项 | 文件 | 验证 |
|---|---|---|
| **B1 ORJSON** | `serve/app/main.py` `default_response_class=ORJSONResponse` | pytest 211 过 |
| **B2 Alembic** | `serve/alembic/` + `alembic.ini`（baseline `cdabba1e3903`）| upgrade head 干净 |
| **B3 多 worker + 预热 + 探针** | `serve/start.sh` PROD 分支 + `main.py` lifespan + `/health/live|ready` | curl 200 |
| **B4 SQL 批量 + N+1** | `serve/app/logics/base.py` `eager_loads` + menu/role 批量删除修复 | pytest 过 |
| **B6 Pydantic response_model** | `serve/app/schemas/base.py` + `controllers/base.py` getList/getDetail | 契约不变 |
| **F1 TanStack Query** | `admin/src/utils/queryClient.ts` + monitor/task_monitor/dashboard | build PASS |
| **F2 PageShell** | `admin/src/components/PageShell/index.vue`（monitor/dashboard 用）| build PASS |
| **F3 StatCard** | `admin/src/components/StatCard/index.vue` | build PASS |
| **F4 CrudTable 扫描** | `admin/scripts/check-crud-usage.mjs` + `check:crud` script | 27 页 9 用 |
| **F5 SchemaCrudPage** | `admin/src/components/SchemaCrudPage/` + `types/crudSchema.ts`（role 页转）| build PASS |
| **F8 ESLint + 错误边界** | `admin/eslint.config.js` + `AppErrorBoundary.vue`（AppMain 包裹）| build PASS |
| **F9 组件文档页** | `admin/src/views/system/docs/index.vue`（待加菜单行）| build PASS |

### 会话前期功能落地

| 项 | 文件 | 状态 |
|---|---|---|
| 全局确认弹窗 | `admin/src/components/AppConfirm` + `utils/confirm.ts` | 🟢 |
| 监控页豪华版 | `admin/src/views/system/monitor/index.vue` | 🟢 |
| CountUp / GaugeBar | `admin/src/components/` | 🟢 |
| 菜单图标统一 | `admin/src/utils/menuIcons.ts`（Sidebar+TagsView 共用）| 🟢 |
| TagsView 右键菜单 | `admin/src/layouts/default/TagsView.vue` | 🟢 |
| 菜单结构调整 | DB：新顶级「用户管理」+ 系统管理/设置沉底 | 🟢 |
| 轮询闪烁修复 | monitor/task_monitor 静默刷新 | 🟢 |
| messages 表漂移修复 | 迁移 `030_messages_rebuild.sql` | 🟢 |
| vue-router 未登录 404 修复 | `admin/src/router/static.ts` | 🟢 |
| keep-alive 缓存修复 | 28 view `defineOptions` + tags store PascalCase | 🟢 |
