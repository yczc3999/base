# Base Platform 基础后台能力补全 TODO

**创建**：2026-08-05 · **复审**：2026-08-05
**背景**：base 当前偏「业务 CRUD 模板」，缺基础后台标配的通用底座能力。base 是下游项目模板，这些能力须在 base 做掉——否则下游各自补，成本放大 N 倍。
**前置**：P0/P1 修复 + 改进 TODO（12 项）已完成。当前 127 后端测试 + vue-tsc + vite build 全绿。

---

## 0. 现状盘点（能力全景）

| 域 | 已有 | 缺失 |
|---|---|---|
| 认证授权 | 登录 / RBAC / 菜单权限 / 操作·登录日志 | 验证码 / 密码策略 |
| 用户管理 | admin_users 后台管理 | **前端用户(users)无后台管理**（查/禁/重置密码） |
| 系统配置 | settings 多分类（ai/sms/storage/notify/...） | 无配置版本历史 |
| 文件 | 上传 / 导出 / 隐私代理 / 静态挂载 | 无文件备份 / 无回收站 |
| 消息 | message（仅 admin 端） | 前端用户无消息入口 |
| 监控 | system_monitor 每 60s 采集 load → Redis `system:metrics` | **无展示页 / 无任务管理 / 无队列监控** |
| 运维 | 手动 `psql -f` 装库，migration runner 可用 | **无定时备份** |
| 数据 | 导出(doExport) + 进度 | **无导入 / 无数据字典** |

---

## 1. 批次 1（P0 · 让 base 真正「基础」）

### B1 · 数据库定时备份

- **目标**：`pg_dump` 定时全库备份 → `storage/backups/`，自动保留 + 后台管理（列表 / 手动备份 / 下载）
- **现状态**：无任何备份机制。`init.sql` + migrations 只在装库时用，运行期数据无保护
- **设计要点**：
  - pg_dump 命令：`PGPASSWORD=$password pg_dump -h $host -p $port -U $user -d $dbname --format=custom --file=$output`（custom 格式支持 pg_restore 选择性恢复；需 PostgreSQL client 工具）
  - 密码通过环境变量 `PGPASSWORD` 传入（不出现于命令行 ps 输出）
  - 保留策略：保留最近 7 天每日 + 最近 4 周每周，旧文件自动清理（删文件 + DB 记录）
  - db_backups 表字段：`id / filename / file_size / status(ok|failed) / started_at / finished_at / error_msg / created_at`
  - 备份验证：备份完成后可选 `pg_restore --list` 校验 dump 完整性（只读 dump 文件头，不实际恢复；pg_restore 缺失则跳过）
  - 恢复：**用户决策 2026-08-05 挂起**——只做备份不做恢复。`pg_restore` 恢复 UI 待后续决策
- **涉及文件**：
  - `serve/app/tasks/db_backup.py` — 新建 BaseTask(interval=86400, name="数据库备份")
  - `serve/app/models/db_backup.py` — 新建：DbBackup(id/filename/file_size/status/started_at/finished_at/error_msg)
  - `serve/app/logics/db_backup.py` — 新建：列表 / 手动触发 / 下载路径 / 删除(含文件)
  - `serve/app/controllers/admin/db_backup.py` — 新建：crud_router + 手动备份 + 恢复(二次确认)
  - `admin/src/views/system/db_backup/index.vue` — 新建：备份列表 + 手动备份按钮 + 下载 + 恢复
  - `serve/databases/migrations/020_db_backups.sql` — 新建
- **验收证据**：
  - 任务定时执行，生成 `.dump`(pg_dump custom 格式)
  - 超过保留策略的旧文件自动删除（DB 记录 + 磁盘文件）
  - 后台可看列表、手动触发、下载文件
  - 恢复操作要求二次确认弹窗
  - `pytest` 新增备份逻辑测试（mock subprocess，验证命令构造）
- **阻塞项**：~~恢复不可逆需 gate 审计~~ → 用户决策只做备份不做恢复，阻塞解除。恢复功能后续独立决策
- **风险/回滚**：pg_dump 只读不破坏；恢复二次确认做最后防线。回滚：删 migration + task + 页面
- **最后更新**：2026-08-05

### B2 · 数据字典（Dict）

- **目标**：集中管理枚举/常量（性别/状态/类型等），前端 DictTag 组件闭环
- **现状态**：无 dict 表、无管理页。`admin/src/components/DictTag/` **不存在**（上轮盘点误判为已建，实为设计规划）
- **表设计**：
  - `dicts`：`id / type_name(VARCHAR 50 UNIQUE) / description / status / created_at / updated_at`
  - `dict_items`：`id / dict_id(FK→dicts.id CASCADE) / value(VARCHAR 100) / label(VARCHAR 100) / sort(INT) / status / created_at`，UNIQUE(dict_id, value)
  - 对外 API：`GET /api/dict/items?type=gender` → `[{value:"1",label:"男"},{value:"2",label:"女"}]`——挂在根路径（非 admin 前缀），不加 `Depends(require_auth)`，天然公开
- **涉及文件**：
  - `serve/databases/migrations/021_dicts.sql` — 新建：dicts + dict_items 两表
  - `serve/app/models/dict.py` — 新建：Dict / DictItem（FK + CASCADE）
  - `serve/app/logics/dict.py` — 新建：CRUD + `get_items_by_type(type_name)`（Redis 缓存永久有效，dict/dict_item 变更时主动失效，与 settings 同模式）
  - `serve/app/controllers/admin/dict.py` — 新建：admin 端 crud_router（需登录）
  - `serve/app/controllers/dict.py` — 新建：`GET /api/dict/items` 公开端点（无 auth，root 路由挂载）
  - `admin/src/views/system/dict/index.vue` — 新建：字典管理页（类型列表 + 项表格编辑）
  - `admin/src/components/DictTag/index.vue` — 新建：接收 `type` prop，调公开端点渲染标签
- **验收证据**：
  - 后台可创建「性别」类型、添加「男/女」字典项
  - `GET /api/dict/items?type=gender` 返回项列表（无需登录）
  - DictTag `<DictTag type="gender" value="1">` → 渲染为「男」
  - `pytest` 新增 dict 逻辑测试
- **依赖**：无
- **风险/回滚**：低，纯新增。回滚：删 migration + 组件 + controller
- **最后更新**：2026-08-05

### B3 · 前端用户（users）后台管理

- **目标**：users 表补 admin 端 CRUD：查看/禁用/重置密码/踢下线
- **现状态**：`users` 表与 `admin_users` 同构（含 `token_version`），但 admin 端无 users 管理。`controllers/client/user.py` 仅登录/注册。`controllers/admin/user.py` 已被 admin_users 占用
- **设计要点**：
  - 控制器挂在 `controllers/admin/client_user.py`（避免与 `user.py` 冲突），路由 `/api/admin/client_user`
  - Logic 加固：`before_create`/`before_edit` 守卫 `is_super_admin`（对齐 admin_user 的 S1 修复），重置密码后调 `revoke_all_tokens`
  - 踢下线：调用 `revoke_all_tokens("client", user_id)`——users 的 scope 是 "client"，删 Redis 中该用户全部 session
  - 重置密码：admin 端输入新密码 → 后端 hash 后写入 → 调 `revoke_all_tokens("client", user_id)` 强制踢下线（与踢下线同一机制，不引入新状态）
- **涉及文件**：
  - `serve/app/logics/user.py` — 补 before_create/before_edit 守卫（pop is_super_admin / token_version）
  - `serve/app/controllers/admin/client_user.py` — 新建：crud_router("client_user", user_logic, perms_prefix="admin:client_user")
  - `admin/src/views/system/client_user/index.vue` — 新建：列表/禁用/重置密码表单
  - 菜单种子 — 补 `admin:client_user:*` 权限点（新增 migration 或追加到 016）
- **验收证据**：
  - admin 端可查看、禁用、重置密码 users 记录
  - 重置密码后该用户所有 client 端 session 失效（Redis 中全部 token 被删除）
  - 非超管按 `admin:client_user:*` 权限点控制
  - `pytest` 新增 client_user 守卫测试
- **依赖**：无
- **风险/回滚**：中——密码重置不可逆。确认重置后强制踢下线。回滚：删 controller + 页面
- **最后更新**：2026-08-05

### B4 · 定时任务 / 队列管理界面

- **目标**：后台查看任务列表（启停/最近执行/手动触发）+ 队列状态展示
- **现状态**：6 个任务自动扫描注册（`tasks/`），**无可视化**。`system_monitor` 采集 load 到 Redis，无展示页
- **设计要点**：
  - 任务列表：扫描 `app/tasks/` 拿 `name/interval/enabled` + Worker 端存最近执行时间到 Redis（`task:last_run:{name}` 含 status/时间/耗时/error）
  - 手动触发：调 `Queue.push` 推入队列，与 BaseTask 定时器推入的完全一致，worker 照常消费（防锁冲突——不绕过 execute 的锁获取逻辑）
  - 队列状态：从 Redis 读各队列长度（`LLEN {prefix}:queue:default/export/notify/task` + `HLEN {prefix}:queue:delayed`）+ processing 残留数
  - 与 P1-4 边界：B4 = 任务表 + 队列，P1-4 = 系统指标页（CPU/内存/磁盘），两个视图可合成一个「运维中心」入口
- **涉及文件**：
  - `serve/app/logics/task_monitor.py` — 新建：扫描任务 + 读执行状态 + 手动手动触发
  - `serve/app/controllers/admin/task_monitor.py` — 新建：任务列表 + 手动触发 + 队列状态端点
  - `admin/src/views/system/task_monitor/index.vue` — 新建：任务表格 + 手动触发按钮 + 队列状态卡片
  - `serve/app/tasks/system_monitor.py` — 增强：加队列长度采集到 `system:metrics`
- **验收证据**：
  - 任务列表显示 6+ 任务（name/interval/enabled/最近执行）
  - 手触发：Queue.push 推入队列 → worker 消费 → execute() 正常获取锁执行
  - 队列状态卡片实时展示各队列长度
  - `vue-tsc -b` + `vite build` 通过
- **阻塞项**：手触发走 Queue.push → worker 消费 → execute() 获取锁后执行（与定时触发路径一致，不绕过锁）
- **风险/回滚**：中——手触发 SEO 管线等重任务可能造成资源竞争。Worker 已有 MAX_CONCURRENT=10 限制。回滚：删页面 + controller
- **最后更新**：2026-08-05

---

## 2. 批次 2（P1 · 锦上添花）

### P1-1 · 在线用户 / 会话管理

- **目标**：查看 admin/client 在线会话列表，一键踢下线（复用 `revoke_all_tokens`）
- **现状态**：Redis `user_tokens:{scope}:{user_id}` 索引已有，`revoke_all_tokens` 已有
- **设计要点**：从 Redis Set 索引读活跃 token 列表 → 拆出 username/user_id/登录时间/最后活跃 → 展示在线用户表
- **涉及文件**：
  - `serve/app/controllers/admin/session.py` — 新建：GET /session/list(读 Redis 索引) + POST /session/kick
  - `admin/src/views/system/session/index.vue` — 新建：在线用户表 + 踢下线按钮
- **验收证据**：页面显示当前在线用户列表；踢下线后该用户 session 消失
- **依赖**：无。工作量 ~2 小时
- **最后更新**：2026-08-05

### P1-2 · 缓存管理界面

- **目标**：后台查看缓存用量（dbsize + 按前缀 key 数），一键清理指定模块缓存
- **现状态**：`BaseLogic.clear_all_cache()` + `cache_del_pattern` 已有，无 UI
- **设计要点**：列出各 Logic 的 `cache_prefix` + 对应 key 数（Redis SCAN 统计）→ 提供「清空此模块缓存」按钮
- **涉及文件**：
  - `serve/app/controllers/admin/cache.py` — 新建：GET /cache/stats(Redis DBSIZE + 按前缀 key 数) + POST /cache/clear(指定 prefix)
  - `admin/src/views/system/cache/index.vue` — 新建：缓存模块卡片 + 清空按钮
- **验收证据**：页面显示各模块 key 数；点击清空后 key 数归零
- **依赖**：无。工作量 ~2 小时
- **最后更新**：2026-08-05

### P1-3 · 数据导入

- **目标**：支持从 Excel/CSV 导入数据到任意已有 CRUD 模块
- **现状态**：导出已有（doExport），导入无
- **设计要点**：模板下载（复用 `export_header_map` 做表头）→ 上传解析 openpyxl → 逐行 validate（复用 `create_rules`）→ 批量 create（独立事务，每行不影响其他行）
- **涉及文件**：
  - `serve/app/utils/import_helper.py` — 新建：Excel 解析 + 逐行校验 + 批量入库
  - `serve/app/controllers/admin/import_api.py` — 新建：模板下载 + 上传导入 + 进度
  - `admin/src/components/ImportModal.vue` — 新建：上传 + 预览 + 提交
- **验收证据**：下载模板 → 填数据 → 上传导入 → 校验通过的行入库，失败行在结果中展示
- **依赖**：openpyxl 已安装。工作量 ~4 小时
- **最后更新**：2026-08-05

### P1-4 · 系统监控展示页

- **目标**：完整系统指标展示页（CPU load / 内存 / 磁盘 / 网络 / 进程 / 队列深度 / Redis 内存）
- **现状态**：`system_monitor` 已采 load → Redis。页面不存在
- **设计要点**：扩展 system_monitor 采集更多指标（内存 free、磁盘 df、Redis INFO memory）→ 前端展示仪表板
- **涉及文件**：
  - `serve/app/tasks/system_monitor.py` — 增强：加内存/磁盘/Redis 指标采集
  - `admin/src/views/system/monitor/index.vue` — 新建：指标仪表板（B4 的任务列表可合入此页或独立页）
- **验收证据**：监控页显示 CPU/内存/磁盘/Redis/队列实时指标，自动刷新
- **依赖**：Redis `system:metrics` key 已有。工作量 ~3 小时
- **最后更新**：2026-08-05

### P1-5 · Migration 管理 UI

- **目标**：后台查看 migration 列表（已执行/待执行），手动执行未运行的 migration
- **现状态**：`python -m app.migrate` CLI 可用（`--list` / `--run` / `--dry-run`），**无 UI**。Migration runner 在 `app/migrate.py`
- **设计要点**：复用 migrate 模块的 `get_pending()` / `run_all()`，封装为 API 端点 → 后台展示 migration 表（文件名 / 状态 / 执行时间）→ 提供「执行全部待执行」按钮（需二次确认）
- **涉及文件**：
  - `serve/app/controllers/admin/migration.py` — 新建：GET /migration/list + POST /migration/run
  - `admin/src/views/system/migration/index.vue` — 新建：migration 表格 + 执行按钮 + 二次确认弹窗
- **验收证据**：页面显示 migration 列表（已执行/待执行状态）；点执行后待执行项消失
- **依赖**：`app/migrate.py` 已有 `get_pending()` / `run_all()`。工作量 ~1.5 小时
- **最后更新**：2026-08-05

---

## 3. 批次 3（P2 · 可选）

| 项 | 说明 | 涉及文件(概要) |
|---|---|---|
| 验证码/登录保护 | 图形验证码或滑块，接入 admin 登录页 | `controllers/admin/user.py` 中 login + 验证码 service |
| 密码策略 | 强制最低复杂度 / 定期过期 | `logics/admin_user.py` 中 change_password 校验 + `settings` 表 |
| 多语言 i18n | 前端硬编码中文 → 配置化 | `admin/src/locales/` + 后端 API 返回字典 key |
| 暗黑模式/主题 | 设计令牌扩展 + CSS 变量切换 | `admin/src/styles/theme.scss` + `stores/theme.ts` |
| 软删除/回收站统一 | 跨模块回收站（article 已有 deleted_at 雏形） | `logics/base.py` 回收站视图 + `views/system/trash/` |
| 前端用户消息入口 | message 表支持 client scope，client 端可读消息 | `logics/message.py` + `controllers/client/message.py` |

---

## 4. 进度追踪

| 项 | 状态 | 预计工作量 | 依赖 | 完成 |
|---|---|---|---|---|
| B1 · 数据库定时备份 | ✅ 完成 (164f3d4) | ~4h | 用户决策: 只备份不恢复 | 2026-08-05 |
| B2 · 数据字典 | ✅ 完成 (93e91cc) | ~3h | — | 2026-08-05 |
| B3 · 前端用户管理 | ✅ 完成 (8bc80d6) | ~2h | — | 2026-08-05 |
| B4 · 任务/队列监控 | ✅ 完成 (ee5a855) | ~3h | — | 2026-08-05 |
| P1-1 · 在线用户 | 待执行 | ~2h | — | — |
| P1-2 · 缓存管理 | 待执行 | ~2h | — | — |
| P1-3 · 数据导入 | 待执行 | ~4h | — | — |
| P1-4 · 系统监控页 | 待执行 | ~3h | — | — |
| P1-5 · Migration UI | 待执行 | ~1.5h | — | — |
| P2-* · 验证码等 | 待决策 | - | — | — |

**总预计**：批次 1 P0 4 项 ~12h，批次 2 P1 5 项 ~12.5h

## 5. 执行顺序建议

```
B2 数据字典 ──→ B3 前端用户管理 ──→ B1 数据库备份 ──→ B4 任务/队列监控
   (独立)         (复用 admin_user 模式)   (需 gate 审计)      (B2/B3 基建可复用)
                                                              ↓
                                              P1-4 系统监控页 + P1-5 Migration UI
                                                              ↓
                                                        P1-1~P1-3 按需
```

- **B2 先做**：最独立，建 dict 表是纯新增，不影响现有功能
- **B3 接着**：复用 admin_user 的守卫/踢人模式，工作量的确最小
- **B1 需要审计**：涉及 `pg_dump` 和恢复，是 P0 里唯一需外部校验的
- **B4 收尾**：任务管理是 P0 里工作量最大的一项，但 B2/B3 的基建（菜单种子/权限点模式）可复用

---

### 审计修正记录（2026-08-05）

| 修正 | 说明 |
|---|---|
| B1 补 pg_dump 命令细节 | 加 `PGPASSWORD` 密码传递、`--format=custom`、保留策略说明 |
| B1 补恢复闭环 | 加 db_backups 表字段 + 二次确认流程 |
| B1 补备份验证 | pg_restore --list 校验 dump 完整性 |
| B2 补 dict 表字段 | 精确到 `dicts`/`dict_items` 每列 + 对外 API `/dict/items` |
| B2 缓存策略修正 | 1h TTL → 永久缓存+变更时主动失效（与 settings 同模式） |
| B2 公开端点路由修正 | `/api/admin/dict/items` → `/api/dict/items`（非 admin 前缀，真正无 auth） |
| B3 补控制器命名 | `client_user` 与 `controllers/client/user.py` 对称，避免与 `user.py` 冲突 |
| B3 踢下线机制统一 | 移除 `token_version++`，统一用 `revoke_all_tokens`（已有且可靠） |
| B4 手动触发矛盾修正 | 明确走 Queue.push + worker 消费（删「直接调 run()」矛盾语句） |
| B4/P1-4 边界明确 | B4=任务列表+队列状态，P1-4=系统指标页，可合为「运维中心」 |
| P1 档格式对齐 | P1-1~P1-4 补精确文件路径 + 验收证据（对齐 B 档格式） |
| 补 Migration UI | P1-5：后台查看/执行 migration（复用已有 migrate.py CLI 能力） |
| 补工作量列 | 每项加预计工时 |
| 补执行顺序图 | ASCII flowchart |
| 补审计修正记录 | 本表 |
