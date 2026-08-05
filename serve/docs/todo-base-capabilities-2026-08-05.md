# Base Platform 基础后台能力补全 TODO

**创建**：2026-08-05
**背景**：base 当前是「业务 CRUD 模板」，缺基础后台标配的通用底座能力。base 是下游项目的模板，这些能力必须在 base 做掉，否则每个下游 fork 后各自补，成本放大 N 倍。
**前置**：P0/P1 修复 + 改进 TODO（12 项）已完成。当前 127 后端测试全绿。

## 现状盘点（能力全景）

| 域 | 已有 | 缺失 |
|---|---|---|
| 认证授权 | 登录/RBAC/菜单权限/操作日志 | 验证码/密码策略 |
| 用户管理 | admin_users 后台管理 | **前端用户(users)无后台管理** |
| 系统配置 | settings 多分类 | 无配置版本历史 |
| 文件 | 上传/导出/隐私代理 | 无备份/无回收站 |
| 消息 | message(仅 admin) | 前端用户无消息入口 |
| 监控 | system_monitor 采集 load | **无展示页/无任务管理/无队列监控** |
| 运维 | 手动 psql 装库 | **无定时备份** |
| 数据 | 导出(doExport) | **无导入/无数据字典** |

---

## 批次 1（P0 · 让 base 真正「基础」）

### B1 · 数据库定时备份

- **目标**：`pg_dump` 定时全库备份 + 保留策略 + 后台管理页（查看列表/手动备份/下载/恢复）
- **现状态**：无任何备份机制。`init.sql` + migrations 只在装库时用，运行期数据无保护
- **涉及文件**：
  - `serve/app/tasks/db_backup.py` — 新建 BaseTask：`pg_dump` 到 `storage/backups/`，按日期命名，保留最近 N 份（如 7 天）
  - `serve/app/models/db_backup.py` — 新建：id/文件名/大小/创建时间/状态
  - `serve/app/logics/db_backup.py` — 新建：列表/手动备份触发/删除
  - `serve/app/controllers/admin/db_backup.py` — 新建：crud_router 注册 + 手动备份端点
  - `admin/src/views/system/db_backup/index.vue` — 新建：备份列表 + 手动备份按钮 + 下载链接
  - `serve/databases/migrations/020_db_backups.sql` — 新建：db_backups 表
- **验收证据**：
  - 任务定时执行生成 .sql/.dump 文件到 storage/backups/
  - 超过保留策略的旧备份自动删除
  - 后台页可看备份列表、手动触发备份、下载
  - `pytest` 新增备份逻辑测试
- **阻塞项**：**涉及生产数据安全**——恢复操作不可逆，需 tri-consensus-gate 审计后再动手
- **风险/回滚**：pg_dump 只读不破坏数据；恢复操作单独加确认 + 二次验证。回滚：删任务文件 + migration 020 反向
- **最后更新**：2026-08-05

### B2 · 数据字典（Dict）

- **目标**：枚举/字典集中管理（性别/状态/类型等），前端 DictTag 闭环
- **现状态**：无 dict 表、无管理页。`admin/src/components/DictTag/` **不存在**（上轮盘点误判为已建，实为设计规划）
- **涉及文件**：
  - `serve/databases/migrations/021_dicts.sql` — 新建：dicts（类型名/说明）+ dict_items（value/label/sort/启用）两表
  - `serve/app/models/dict.py` — 新建：Dict / DictItem 两模型
  - `serve/app/logics/dict.py` — 新建：dict CRUD + get_by_type（按类型取项列表）
  - `serve/app/controllers/admin/dict.py` — 新建：crud_router 注册
  - `admin/src/views/system/dict/index.vue` — 新建：字典管理页（类型分组 + 项编辑）
  - `admin/src/components/DictTag/` — 新建：按 dict_type 渲染标签
- **验收证据**：
  - dicts + dict_items 两表 CRUD 正常
  - 前端可创建字典类型、维护字典项
  - DictTag 组件按类型渲染（有数据源）
  - `pytest` 新增 dict 逻辑测试
- **依赖**：无
- **风险/回滚**：低，纯新增。回滚：删 migration + 组件
- **最后更新**：2026-08-05

### B3 · 前端用户（users）后台管理

- **目标**：`users` 表（前端用户）补后台 CRUD：查看/禁用/重置密码/踢下线
- **现状态**：`users` 表与 `admin_users` 同构（含 token_version），但 admin 端**无任何 users 管理**。`controllers/client/user.py` 只有登录/注册/信息
- **涉及文件**：
  - `serve/app/logics/user.py` — 补 before_create/before_edit 守卫（同 admin_user：禁 token_version 等）
  - `serve/app/controllers/admin/user_manage.py` — 新建：crud_router("user") 挂前端 users 表
  - `admin/src/views/system/user_manage/index.vue` — 新建：前端用户管理页（列表/禁用/重置密码）
  - `serve/databases/migrations/016_content_menus_perms.sql` 或新增 — 补 users 管理菜单 + 权限点
- **验收证据**：
  - admin 端可查看 users 列表、禁用、重置密码
  - 复用 `token_version` 踢下线
  - 非超管按 admin 权限点控制
  - `pytest` 新增 user_manage 逻辑测试
- **依赖**：无
- **风险/回滚**：中——前端用户管理涉及密码重置（不可逆）。需确认重置后强制重新登录。回滚：删 controller + 页面
- **最后更新**：2026-08-05

### B4 · 定时任务/队列管理界面

- **目标**：后台查看任务列表（启停状态/最近执行/手动触发），队列长度/失败数展示
- **现状态**：6 个任务自动扫描注册（tasks/），但**无任何可视化**。`system_monitor` 每 60s 采集 load 到 `system:metrics`（Redis），已有数据源但无展示页
- **涉及文件**：
  - `serve/app/logics/task_monitor.py` — 新建：扫描 tasks/ 列出任务（name/interval/enabled）+ 读取最近执行状态
  - `serve/app/controllers/admin/task_monitor.py` — 新建：任务列表端点 + 手动触发端点
  - `admin/src/views/system/task_monitor/index.vue` — 新建：任务列表 + 手动触发按钮 + 队列状态
  - `serve/app/tasks/system_monitor.py` — 增强：采集 Redis 队列长度 + 更多系统指标
  - `admin/src/views/system/monitor/index.vue` — 新建：系统监控展示页（读 system:metrics）
- **验收证据**：
  - 任务列表显示全部 6+ 任务（name/interval/enabled）
  - 手动触发一个任务生效
  - 监控页显示 load/CPU/队列长度
  - `vue-tsc -b` + `vite build` 通过
- **阻塞项**：手动触发任务需防重复（复用 BaseTask 锁）
- **风险/回滚**：中——手动触发可能重复执行（SEO 管线等重任务）。需确认手动触发走独立队列且限并发。回滚：删页面 + controller
- **最后更新**：2026-08-05

---

## 批次 2（P1 · 锦上添花）

### P1-1 · 在线用户/会话管理

- **目标**：查看在线 admin 会话列表，一键踢下线（复用 revoke_all_tokens）
- **涉及文件**：`serve/app/controllers/admin/session.py` + `admin/src/views/system/session/index.vue`
- **依赖**：Redis `user_tokens:{scope}:{user_id}` 索引已有

### P1-2 · 缓存管理界面

- **目标**：查看缓存用量，一键清理指定模块缓存
- **涉及文件**：`serve/app/controllers/admin/cache.py` + `admin/src/views/system/cache/index.vue`
- **依赖**：BaseLogic.clear_all_cache 已有

### P1-3 · 数据导入

- **目标**：为支持导出的模块补导入（模板下载 + 上传解析 + 校验）
- **涉及文件**：`serve/app/controllers/admin/import.py` + `admin/src/components/ImportModal.vue`

### P1-4 · 系统监控展示（独立于 B4）

- **目标**：完整系统监控页（CPU/内存/磁盘/网络/进程）
- **涉及文件**：`serve/app/tasks/system_monitor.py` 增强 + `admin/src/views/system/monitor/index.vue`
- **注**：与 B4 的任务管理界面可合并为一个「运维中心」

---

## 批次 3（P2 · 可选）

| 项 | 说明 |
|---|---|
| 验证码/登录保护 | 图形验证码或滑块 |
| 密码策略 | 强制复杂度/定期过期 |
| 多语言 i18n | 前端硬编码中文 → 配置化 |
| 暗黑模式/主题 | 设计令牌扩展 |
| 软删除/回收站统一 | 跨模块回收站 |
| 前端用户消息入口 | message 表支持 client scope |

---

## 进度追踪

| 项 | 状态 | 决定 | 完成 |
|---|---|---|---|
| B1 · 数据库定时备份 | 待执行 | 需 tri-consensus-gate | — |
| B2 · 数据字典 | 待执行 | — | — |
| B3 · 前端用户管理 | 待执行 | — | — |
| B4 · 任务/队列监控 | 待执行 | — | — |
| P1-1 · 在线用户 | 待执行 | — | — |
| P1-2 · 缓存管理 | 待执行 | — | — |
| P1-3 · 数据导入 | 待执行 | — | — |
| P1-4 · 系统监控页 | 待执行 | — | — |
| P2-* · 验证码等 | 待决策 | — | — |

## 执行顺序建议

1. **B2 数据字典**（最标准、最独立，先做建立信心）
2. **B3 前端用户管理**（复用 admin_user 模式，快）
3. **B1 数据库备份**（涉及安全，需审计，放第三）
4. **B4 任务/队列监控**（依赖 B1 的部分基建）
5. 批次 2 视精力
