# Base Platform 改进 TODO 文档

**创建**：2026-08-05
**范围**：架构改进 / 工程化提升 / 代码优化（非 bug 修复）
**前置**：P0/P1 修复已完成（commit `d3ca76a` + `e930e65`）

## 目录

- [A 档：高价值 · 低风险 · 可立即执行](#a-档高价值--低风险--可立即执行)
  - [A1 · 核心管道测试（0 → 有）](#a1--核心管道测试0--有)
  - [A2 · 分页深度防护](#a2--分页深度防护)
  - [A3 · 导出流式化](#a3--导出流式化)
- [B 档：中期工程化](#b-档中期工程化)
  - [B1 · 建 migration runner](#b1--建-migration-runner)
  - [B2 · 缓存一致性（双删）](#b2--缓存一致性双删)
  - [B3 · `get_list` DB 异常可见性](#b3--get_list-db-异常可见性)
  - [B4 · 缓存穿透防护（负缓存）](#b4--缓存穿透防护负缓存)
- [C 档：架构级 · 需要决策](#c-档架构级--需要决策)
  - [C1 · 关系层落地（FK + relationship + 级联）](#c1--关系层落地fk--relationship--级联)
  - [C2 · 统一 SSE 逻辑](#c2--统一-sse-逻辑)
- [补充：遗漏 NIT 小改进](#补充遗漏的-nit-小改进审计发现但未入档)
  - [N1 · Validator 类型归一修复](#n1--validator-类型归一修复)
  - [N2 · require_perms 避免额外 DB session](#n2--require_perms-避免额外-db-session)
  - [N3 · 文档漂移同步](#n3--文档漂移同步)

---

## A 档：高价值 · 低风险 · 可立即执行

### A1 · 核心管道测试（0 → 有）

- **目标**：为核心管道（BaseLogic / crud_router / QueryHelper / Token）建立至少 20 个单测，防回归
- **现状态**：项目零自动化测试（serve 无 tests/，admin 无 test/）。所有验证靠 `py_compile` + 手动启动
- **涉及文件**：
  - `serve/app/logics/base.py` — BaseLogic CRUD/缓存/软删
  - `serve/app/controllers/base.py` — crud_router 路由生成（需 FastAPI TestClient）
  - `serve/app/utils/query.py` — QueryHelper 35 操作符
  - `serve/app/utils/token.py` — Token 生成/刷新/登出/全部撤销
  - `serve/app/tasks/base.py` — BaseTask 防重复锁
  - `serve/requirements-dev.txt` — 新建，写入 pytest / pytest-asyncio / httpx
- **验收证据**：
  - `serve/tests/` 目录存在
  - `cd serve && python -m pytest tests/` 全绿
  - 覆盖：CRUD 白名单过滤 / 创建/编辑/删除 / 软删过滤 / QueryHelper 15+ 操作符 / Token 创建+超时+登出+全撤 / BaseTask 锁 token
- **阻塞项**：无
- **非目标**：不测 Service 驱动层（需要外部服务商凭证，留在后期集成测试）；不测 controller 的鉴权（需要完整的 RBAC 种子+Redis session）
- **依赖**：`pip install pytest pytest-asyncio httpx`
- **风险/回滚**：测试用内存 SQLite 或 mock DB，数据隔离无风险。如果 `pytest` 与现有依赖冲突，回滚 `requirements-dev.txt`
- **最后更新**：2026-08-05

### A2 · 分页深度防护

- **目标**：`page` 参数加最大限制，防止 `offset` 无限深扫
- **现状态**：`logics/base.py:121` `page = max(1, int(...))` 无上限。恶意构造 `page=9999999` → offset 数千万 → DB 扫全表
- **涉及文件**：
  - `serve/app/logics/base.py` — `get_list` 方法 ~121 行 + 新增 `MAX_PAGE` 常量
- **验收证据**：
  - `page` 超过 `MAX_PAGE`（建议 100000）时返回 `{"code":1, "msg":"超出分页上限"}`，不执行查询
  - `page` 在合法范围内正常分页
  - `python -m py_compile serve/app/logics/base.py` 通过
- **阻塞项**：无
- **非目标**：不做 keyset 分页（架构改动大，当前 pageSize≤100 足够）
- **依赖**：无
- **风险/回滚**：修改 base.py 一行，git revert 即可
- **最后更新**：2026-08-05

### A3 · 导出流式化

- **目标**：单文件导出去掉 `all_rows.extend(chunk)` 全量累积内存，改为逐批写 xlsx 的生成器模式
- **现状态**：`export_helper.py:157-173` `_fetch_all_chunks` 把 `all_rows.extend(chunk)` 全量进内存（≤200000 行 = 所有字段 dict），然后一次性 `_write_xlsx`。实际 `openpyxl write_only` 已经支持流式写入，只是上层构造了一个完整 list 再传入
- **涉及文件**：
  - `serve/app/utils/export_helper.py` — `_fetch_all_chunks` 重构为 `_stream_write_xlsx`（边查边写，不保留全量）
- **验收证据**：
  - `_fetch_all_chunks` 不再构建 `all_rows` 列表
  - 单文件导出内存占用与行数解耦（Openpyxl write_only 自身常量内存）
  - 导出 1000 行验证通过（可以用已有 `file_key` 流程测试）
- **阻塞项**：`_write_xlsx` 被单文件路径（`_export_single`）和 ZIP 路径（`_export_zip:143`）两处调用——改造需兼容 ZIP 路径仍接受 `list[dict]`（最多 MAX_ROWS 行，已分批），单文件路径改为逐 chunk 直接写。重构范围：`_export_single` 不再调用 `_write_xlsx`，而是循环 `_fetch_chunk_batch` → 直接 `ws.append`
- **非目标**：不重构 ZIP 多文件路径（已按 MAX_ROWS 分批，风险可控）
- **依赖**：无
- **风险/回滚**：中等——导出是用户可见功能。建议做 A1 后再改 A3（有测试防回归）。回滚：git revert
- **最后更新**：2026-08-05

---

## B 档：中期工程化

### B1 · 建 migration runner

- **目标**：让 migrations/ SQL 文件能通过 `python -m app.migrate` 一次性执行，替代手动 `psql -f`
- **现状态**：`serve/databases/migrations/001-016` 是裸 SQL 文件，无执行记录表。016 已建但**未在任何环境应用**。CLAUDE.md 装库指引是 `psql -f databases/init.sql`
- **涉及文件**：
  - `serve/app/migrate.py` — 新建，扫描 migrations/，按序执行，幂等跳过
  - `serve/databases/migrations/017_schema_migrations.sql` — 新建，创建 `schema_migrations` 表（DB 表，用于记录已执行迁移版本）
- **验收证据**：
  - `python -m app.migrate` 执行 001-016，全部成功或幂等跳过
  - 重复执行无副作用（`schema_migrations` 表记录已执行）
  - 新装库流程：`psql -f init.sql → python -m app.migrate`
- **阻塞项**：需要连接 DB（不能纯文件操作），需要在 `.env` 就绪的环境下使用
- **非目标**：不做 alembic（零框架依赖原则）。migration 回滚不在范围（SQL 文件天然不回滚）
- **依赖**：base.py 数据库连接已就绪
- **风险/回滚**：读 migrations 按文件名排序，需确保 001-016 文件名已规范排序（当前是 3 位补零）。如果某条 SQL 有语法错误会部分失败——需迁移 runner 支持事务（每条 migration 包在 BEGIN/COMMIT 中）
- **最后更新**：2026-08-05

### B2 · 缓存一致性（双删）

- **目标**：解决并发修改时的缓存旧值问题
- **现状态**：`logics/base.py:290-318` `modify` 中 `_clear_cache`（290 行）在 `commit`（311 行）**之前**执行。并发读可能打到旧行并重新缓存旧值
- **涉及文件**：
  - `serve/app/logics/base.py` — `modify` 方法：commit 之后延迟再删一次缓存（`asyncio.create_task` 延迟 100ms 再删）
- **验收证据**：
  - `modify` 方法 commit 后追加异步延迟清缓存
  - 缓存 key 在 commit 后再次被删除（即使并发读重新写入也会被延迟清掉）
- **阻塞项**：`_clear_cache` 需要读旧记录（查 `cache_fields` 值）。双删后第二次清需要仍能拿到 cache key 列表
- **非目标**：不做分布式锁/版本号方案（过度设计）
- **依赖**：无
- **风险/回滚**：低。延迟清缓存失败不影响数据正确性。注意 `asyncio.create_task` 在 FastAPI 请求结束后可能被取消——建议用 `asyncio.ensure_future` 或 `loop.call_later`，或直接用 Redis `EXPIRE` 缩短 TTL 代替第二次主动删除。回滚：git revert
- **最后更新**：2026-08-05

### B3 · `get_list` DB 异常可见性

- **目标**：DB 故障不要伪装成「无数据」，要记日志并返回 500
- **现状态**：`logics/base.py:165-170` catch-all `except Exception` 吞掉连接超时、表锁等故障，返 `{"list":[], "total":0}`（code 0），故障不可观测
- **涉及文件**：
  - `serve/app/logics/base.py` — `get_list` 方法 165-170 行
- **验收证据**：
  - DB 异常（如连接断开）→ 错误日志 + 返回 `{"code":500, "msg":"查询失败"}`
  - 空结果（数据真为空）→ 正常 `{"list":[], "total":0}`（code 0）
  - 区分逻辑：`except Exception` 里 `logger.error()` + 抛出 BizError(500) 由全局 handler 处理
- **阻塞项**：需确认所有调用方（crud_router + 自定义 controller）能处理 500 响应
- **非目标**：不修改 `get_detail`/`get_by_field`（这些返回 None 有语义含义）
- **依赖**：无
- **风险/回滚**：如果某个调用方依赖「异常返回空列表」的行为（比如前端判断 `list.length===0` 决定空态 UI），修复后会看到错误提示。需验证前端是否有这种情况。回滚：git revert
- **最后更新**：2026-08-05

### B4 · 缓存穿透防护（负缓存）

- **目标**：不存在的 pk/field 查询结果做短 TTL 负缓存，防止恶意遍历
- **现状态**：`logics/base.py:217-225` `get_detail`、`237-250` `get_by_field` 对不存在的结果直接返回 None，无任何缓存。恶意遍历不存在的 id 每次都打 DB
- **涉及文件**：
  - `serve/app/logics/base.py` — `get_detail` 和 `get_by_field` 增加空结果负缓存（TTL 30s，存特殊 marker 如 `__NULL__`）
- **验收证据**：
  - 查询不存在的 `id=99999` — 第一次查 DB，返回 None
  - 30 秒内重复查询 `id=99999` — 不查 DB，直接返回 None
  - 真实创建 `id=99999` 的记录后——创建/编辑清空该 key 的负缓存（`_set_cache` 里判断）
- **阻塞项**：负缓存 marker 需要和真实数据区分（防止把 `__NULL__` 当业务数据返回）。建议缓存 `{"__null__": True}` 字典，在 `get_detail` 的缓存命中路径判断 `cached.get("__null__")`
- **非目标**：不做全量预填 null（内存/复杂度不值得）
- **依赖**：无
- **风险/回滚**：如果负缓存创建后记录被数据迁移脚本直接写 DB（绕过 BaseLogic create），缓存不一致 30 秒。风险极低。回滚：git revert
- **最后更新**：2026-08-05

---

## C 档：架构级 · 需要决策

### C1 · 关系层落地（FK + relationship + 级联）

- **目标**：补 DB 外键约束 + 级联删除（引用完整性）+ SQLAlchemy relationship（关系可导航）
- **现状态**：`role_menus`/`admin_user_roles` 是裸 Integer 主键，无 FK。删 role（有应用层守卫）/menu/user 会留孤儿行（DB 层不阻止）。`article_keywords` 已有 FK + CASCADE（015 已加）。全 model 零 `relationship()` 声明
- **科学分析**：FK 是引用完整性的不可绕过保证，应用层守卫可被绕过，两者互补。Base 先固化通用约束；具体项目在 fork/clone 后按自身 schema 独立扩展。关联表用 CASCADE 是关系建模标准（无独立生命周期）
- **非夸大说明**：relationship 不解决 N+1（默认 lazy 反而可能引入）；N+1 的解法是 selectinload/joinedload，属另一层工作。C1 只保证完整性 + 关系可导航
- **涉及文件**（影响 12 个 model + 2 个 SQL + 1 个 migration）：
  - `serve/app/models/role_menu.py` — 加 FK(roles.id) + FK(menus.id) + relationship
  - `serve/app/models/admin_user_role.py` — 加 FK + relationship
  - `serve/app/models/article_keyword.py` — 已有 FK，补 relationship
  - `serve/app/models/keyword.py` — 补 `articles` relationship（通过 article_keywords）
  - `serve/app/models/article.py` — 补 `keywords` relationship
  - `serve/app/models/menu.py` — 补 `parent`/`children` relationship（自引用）
  - `serve/app/models/admin_user.py` — 补 `roles` relationship + `logs`/`login_logs`
  - `serve/app/models/role.py` — 补 `menus` relationship
  - `serve/app/models/message.py` — 补 `user` relationship
  - `serve/app/models/file.py` — 补 `user` relationship
  - `serve/app/logics/menu.py` — 改 `get_tree`/`get_tree_by_role_ids` 用 relationship 而非裸 join
  - `serve/databases/migrations/019_add_foreign_keys.sql` — 新建，补 FK 约束 + 级联策略
- **验收证据**：
  - 所有 model 的 `__init__.py` relationship 导入可用
  - migration 017 可执行（`ALTER TABLE ADD CONSTRAINT ... FOREIGN KEY ... ON DELETE ...`）
  - 删 role → role_menus 自动被清理（`ON DELETE CASCADE`）
  - 删 admin_user → admin_user_roles 自动被清理
  - `admin_user.roles` → 懒加载 role 列表
- **阻塞项**：
  - 现有数据可能有孤儿行——加 FK 前需先清理（`DELETE FROM role_menus WHERE menu_id NOT IN (SELECT id FROM menus)` 等）
  - `ON DELETE CASCADE` 行为变更——删 menu 不再静默留孤儿，需确认业务约定
- **非目标**：不做 eager loading 全局配置（逐个查询决定 `selectinload`/`joinedload`）
- **依赖**：A1（测试）——有测试后才敢动 model 层
- **风险/回滚**：**高**。FK 约束加错会阻止 DML。建议先补 relationship（纯 Python 侧，无 DDL），再补 FK（需要 migration）。回滚：migration 018（删 FK 约束）
- **最后更新**：2026-08-05

### C2 · 统一 SSE 逻辑

- **目标**：article/keyword 两处的 SSE 流式读取抽成 `useSSE` hook
- **现状态**：`article/index.vue` `runSSE`（~50 行）与 `keyword/index.vue` `startSSE`（~80 行）逻辑几乎相同（ReadableStream + getReader + 逐行解析 + 进度回调），重复 4 处引用（10 行 grep 命中）。代码重复、出错点分散
- **涉及文件**：
  - `admin/src/hooks/useSSE.ts` — 新建，抽取 SSE 流式读取 hook
  - `admin/src/views/content/article/index.vue` — 改用 `useSSE`
  - `admin/src/views/content/keyword/index.vue` — 改用 `useSSE`
- **验收证据**：
  - `useSSE.ts` 接收 `url: string, onChunk: (data) => void, onDone?: () => void` 参数
  - article/keyword 两页删除本地 SSE 实现，改用 hook
  - `vue-tsc -b` 通过
  - 页面功能不变（stream 生成/采集进度正常）
- **阻塞项**：article 的 `runSSE` 有特殊的换行符处理（`\r\n\r\n`），keyword 的 `startSSE` 也有。需要设计通用协议适配
- **非目标**：不处理 EventSource（GET 长连接）模式——当前只有 POST 流式
- **依赖**：无
- **风险/回滚**：低。hook 抽提不影响 UI 渲染。回滚：git revert
- **最后更新**：2026-08-05

---

## 补充：遗漏的 NIT 小改进（审计发现但未入档）

以下 3 项在审计中已发现但未列入修复。价值中等，改动量小，可随时顺手做。

### N1 · Validator 类型归一修复

- **目标**：修复 3 个 validator 规则的边缘行为
- **现状态**：审计 R4-C 发现 `validator.py:163-190` 三个问题：`in`/`not_in` 用 `str(value)` 比较导致布尔值匹配错误；`boolean` 规则不收 "True"/"False" 字符串；`regex` 用 `re.match`（非全匹配，`re.search` 语义）
- **涉及文件**：`serve/app/utils/validator.py`
- **改动量**：~15 行
- **验收证据**：`python -m pytest tests/test_validator.py`（如有 A1）；或手动 `python -c` 验证 True 能被 `in` 规则正确匹配

### N2 · require_perms 避免额外 DB session

- **目标**：消除权限校验时的双 session 开销
- **现状态**：`deps.py:122` `_check_perms` 内 `async with async_session()` 创建额外 session，而调用方（如 crud_router）已经持有请求级 session
- **涉及文件**：`serve/app/deps.py`
- **改动量**：~10 行（`_check_perms` 接受传入 session 参数）
- **验收证据**：crud_router 各端点权限校验仍正常，不再双连 DB

### N3 · 文档漂移同步

- **目标**：7 处文档宣称与代码实现对齐
- **现状态**：`service-design.md` 6 处漂移（阿里云签名 V1→V3、interface.py 不存在、SMS driver_map 含 qiniu、broadcast 宣称并行实为串行、s3 URL 格式、热重载重注册 vs 整进程重启）+ `queue-task-design.md` 1 处（防重复 get+set 已迁 SET NX）
- **涉及文件**：`serve/docs/service-design.md`、`serve/docs/queue-task-design.md`
- **验收证据**：`grep` 对比文档描述与实际代码，7 处全部对齐

---

## 进度追踪

| 项 | 状态 | 决定 | 完成 |
|---|---|---|---|
| A1 · 核心管道测试 | ✅ 完成 | — | 78 测试 + coverage 68% |
| A2 · 分页深度防护 | ✅ 完成 | — | MAX_PAGE=100000 |
| A3 · 导出流式化 | ✅ 完成 | — | 逐 chunk 写 xlsx |
| B1 · migration runner | ✅ 完成 | — | python -m app.migrate |
| B2 · 缓存一致性 | ✅ 完成 | — | commit 后延迟双删 |
| B3 · get_list 异常可见 | ✅ 完成 | — | 抛 BizError(500) |
| B4 · 缓存穿透防护 | ✅ 完成 | — | 30s 负缓存 |
| C1a · FK + 级联 | ✅ 完成 | 关联表 CASCADE | migration 019 + 4 测试 |
| C1b · relationship | ✅ 完成 | 关联表+自引用 | 10 model 双向 relationship + passive_deletes；commit b437d94 补全 6 model（article/keyword/article_keyword/message/file/admin_user.logs）；get_tree 维持单查询组树（已无 N+1，relationship 模型层可用） |
| C2 · 统一 SSE | ✅ 完成 | — | useSSE hook |
| N1 · Validator 类型归一 | ✅ 完成 | — | 30 测试 |
| N2 · require_perms 双 session | ✅ 完成 | — | 复用请求 session |
| N3 · 文档漂移同步 | ✅ 完成 | — | 7 处对齐 |
