# Base Platform 对抗性复核报告（2026-08-04，对比 review-2026-08-04.md）

**方法**：对上次审计 ~140 条发现做**自我对抗**验证——主会话亲读安全/核心项 + 3 个对抗代理（模型/DB、服务/任务、前端）专门「试图推翻」其余域。每条给出三态裁决：CONFIRMED（尝试反驳后无法推翻）/ REFUTED（误报，含反例）/ PARTIAL（成立但机制/范围/严重级需修正）。

## 0. 总览

| 裁决 | 数量 | 说明 |
|---|---|---|
| REFUTED | 10 | 上次误报或已降级 |
| PARTIAL | 11 | 成立但机制/范围/严重级修正 |
| CONFIRMED | ~100 | 核心维持（含 3 处外部规范/官方样例复核） |
| NEW | 8 | 上次遗漏，本次对抗中发现 |

**净结论**：上次 3 个 Critical 中 **S2、S3 维持**，S1 **收窄攻击面但仍是 Critical**（编辑路径误报、创建路径实锤）。同时推翻 2 个「必炸」级后端结论（状态机 result、SEO 管线整批回滚）。核心安全结论不变，但具体攻击路径与严重级有重要修正。

---

## 1. 推翻清单（REFUTED — 上次误报）

| # | 上次结论 | 反例证据 | 修正 |
|---|---|---|---|
| R-1 | S1 编辑现有用户可自提超管 | `logics/admin_user.py:40` `before_edit` 执行 `data.pop("is_super_admin")`——编辑路径被守卫 | 误报。真实向量是**创建**（见 PARTIAL-1） |
| R-2 | S4c 导出无视归属当前即泄露 | 全站仅 operation/login 日志开启导出（`export_header_map` 覆写），两者均无 bind_user_column；file/message 有归属但未开导出 | 潜伏架构隐患，当前不触发 |
| R-3 | B-C2 date_eq 与 date_gt 语义不一致 | `query.py:267` date_eq 前缀匹配整日、date_gt 当日零点后——行为自洽；代码只是注释写「cast DATE」实际 cast String | 纯 NIT，非 bug |
| R-4 | handle_export logic_path 可 RCE/数据泄露 | `controllers/base.py:223` logic_path 由 `type(logic).__module__` 服务端生成，非用户输入 | 误报 |
| R-5 | R1 删 role 留孤儿行 | `logics/role.py` `do_delete` 覆写：先清 role_menus + 有关联用户时阻止删除 | 仅 menu/admin_user 仍孤儿 |
| R-6 | seo_simplify.sql 重跑安全 | `TO_REGCLASS` 守卫放 FROM 子句，PG 解析期即因 `relation "publish_schedule" does not exist` 崩溃——只能跑一次 | 误报，且守卫手法不可靠 |
| R-7 | 状态机「失败时 result 常为 False」 | `services/base.py:73-77` `_fail` 设 `_result=None`，`self.result` 属性确实为 None，文档契约成立；上次把方法返回值误当 result 属性 | 误报（至多 NIT 二义性） |
| R-8 | seo_pipeline 单 tag 失败回滚整批草稿 | `logics/article.py:125`、`article_keyword.py:37,52` 每 tag 已独立 commit；失败仅中止后续循环，不丢已生成草稿 | 误报（残余：异常未捕获 + 孤儿草稿，NIT） |
| R-9 | 前端 keyword 采集引擎 duckduckgo/ddg 不一致 | `keyword_harvester.py:123-128` `ENGINE_MAP["duckduckgo"]=("…","ddg")` 后端桥接：入参用引擎键、落库用 ddg，前端按 ddg 展示——两端对接不同层 | 误报 |
| R-10 | 前端 handleEdit 密码 hash 回传 | `logics/admin_user.py:14` `except_keys=["password"]` + `logics/base.py:389-391` 剥敏感，getDetail 响应无 password | 误报 |

## 2. 修正清单（PARTIAL — 机制/范围/严重级修正）

| # | 原结论 | 修正后事实 | 严重级 |
|---|---|---|---|
| P-1 | S1 mass-assignment 自提超管 | **编辑向量误报**（R-1），**创建向量实锤**：`before_create:33` 不 pop is_super_admin，有 `admin:user:create` 者可建超管；全站无字段白名单属实 | 仍 CRITICAL（向量收窄） |
| P-2 | B-F1 message 删除必炸（TypeError） | 核心成立（删除永远不工作），**机制修正**：可选链短路为静默 no-op，非 TypeError；`markRead` 的 getList 同样 no-op（标已读不刷新） | 功能失效（非崩溃） |
| P-3 | B-F2 FileUpload rsplit 必炸 | `rsplit` 确实不存在，但 `<FileUpload>` 当前**无任何视图引用**（仅自动注册） | 潜伏（死组件内） |
| P-4 | article 双套接口约定，后端需兼容 | `edit.vue` 用 `/detail\|create\|edit` 三端点**均 404**，但该页面是孤立死代码（无菜单/路由入口） | 死代码问题，非兼容问题 |
| P-5 | CrudTable 声明未实现 image/dateRange/treeSelect | 声明-实现缝隙属实，但全站**无视图使用**这三种类型 | 死声明（零运行影响） |
| P-6 | 命名队列永不消费 | 无命名队列生产者；延迟任务经 `_check_delayed` 实际路由到 default 被消费 | 能力空洞（当前 0 丢消息） |
| P-7 | B-S4 LocalDriver 路径穿越 | 前缀绕过属实，但可达性需 admin 权限 + 未校验的 `category` Form 参数，越界仅限 base 前缀兄弟目录 | BUG 成立，影响面收窄 |
| P-8 | worker close_redis 从未被调用 | `main.py` lifespan 确实调用；仅 **worker 进程**退出不释放 | 降级（worker-only） |
| P-9 | G5 article_keywords delete+insert 非原子 | 实为**单事务**（一次 commit，崩溃整体回滚）；真风险仅并发写写竞争 + primary_id 可能缺失 | 约束缺失成立，原子性夸大 |
| P-10 | R1 关联表无外键留孤儿 | role 有守卫（P 清关联+阻止）；**menu 删除不清理 role_menus、admin_user 删除不清理 admin_user_roles** 仍孤儿 | 面收窄至 menu/user |
| P-11 | B-D1/B-D2 全新库「必炸」 | 特指 **migrations-only** 路径；按 CLAUDE.md（init.sql+域名 SQL）装库则 publish_log/tags 先建表、015 守卫可过。但 001-014 与 init.sql 表重复、两路径互斥，migrations/ 是唯一可独立执行路径 | CONFIRMED 加边界注记 |

## 3. 确认清单（CONFIRMED — 核心维持）

### 安全（P0）
- **S2** `/setting/get`+`/setting/set` 仅 `require_admin`（admin scope 内无细分权限）→ 任意非超管管理员可读写全部配置含 AI/GSC/IndexNow 密钥；`settings:all` 明文缓存 Redis 300s；`config.py` 凭据非 SecretStr。**维持 HIGH**（需 admin 账号，非匿名）。
- **S3** `/file/upload` 不传 allowed_types（`file.py:33-38`）→ 任意 MIME + 无 size 上限；经 `/uploads` 静态挂载或 OSS public 直出。**维持 HIGH**。
- **S4a** 编辑 IDOR：file/message 设 bind_user_column，但 crud_router `_do_edit` 更新路径不校验归属（`controllers/base.py:153-174`）。
- **S4b** `/file/batchDelete` 调 `delete_files` 无 user_id 过滤（`file.py:67-82`），与列表按归属过滤不一致。
- **S5** 登出只删 access_token（`token.py:151-171`），refresh_token 存活 7 天；refresh 不轮换、无重用检测；client 登录/注册无限流。

### 后端核心
- 软删除不被通用 CRUD 尊重（article 列表/详情含已删）。
- 敏感数据（含 password hash）以 `with_sensitive=True` 落 Redis 明文。
- create/modify 把 DB 基础设施错误包装成 BizError(400) + `err_msg[:200]` 外泄 DB 细节。
- `get_list` 吞掉全部 DB 异常返回空列表（故障伪装成无数据）。
- `time_eq` 只比小时（`query.py:287-289`），与 time_gt/lt 族语义不一致。**B-C1 维持**。
- 无效 JSON body → 500；getDetail/doDelete 硬编码 int；modify 跳过 None。
- require_perms 每次额外开 DB session（`deps.py:122`）；`auth_header[7:]` 不 strip。

### 控制器/路由
- B-R1 隐私代理路径基准不一致（写 `serve/storage/private`、读 local 配置 path）→ 默认必 404；`path` 前缀还带双重 private。
- B-R2 `/uploads` 静态挂载与本地驱动默认 path 不一致 → 默认安装上传不可达。
- B-R3 控制器裸写 SQL 绕过 article_logic。
- G-R1 users 表无后台管理；G-R2 article_keywords 无管理接口；G-R3 消息语义错位；G-R4 RBAC 权限点大量未种 → 非超管整体 403。

### 模型/数据库
- B-D1 publish_log 无 migration（唯一建表在 seo.sql:108）。
- B-D2 migrations 015 在全新库顺序执行必炸（tags/articles 不在 migrations）+ **FK BIGINT→INT 可能 DDL 即失败**。
- B-D3 tag.sql 全死、article.sql/seo.sql 过期区块重跑复活死表（`IF NOT EXISTS`）。
- G1/G2/G3/G4/G6/G7、R2/R3/R4/R5 及全部 NIT 维持。

### 服务/任务
- **B-S1 qcloud_cos 用 HMAC-SHA256——外部规范已确认 SHA1，请求必鉴权失败**（`qcloud_cos.py:117,122`）。
- **B-S2 华为短信 WSSE PasswordDigest 偏离官方样例——官方样例已确认，鉴权必失败**（`huawei.py:48-50`）。
- BaseTask 锁无持有者 token（跨 worker 抢锁/误删）；processing 无孤儿恢复；无重试/死信；send_sms 缺参静默 ACK；凭据明文 settings 表 + Redis 缓存；满载阻塞延迟队列；utcnow 弃用（修法非一行）；storage.url() 掩盖失败（潜伏）。
- 文档漂移 8 项全维持（interface.py 不存在、SMS driver_map 含 qiniu、阿里云 V1/V3、s3 URL、broadcast 串行、热重载、防重复）。

### 前端
- B-F3 setQuery 未实现（统计卡片筛选全 no-op）；B-F4 表单 default 不生效；B-F5 双弹 toast；**B-F6 退出登录不重置 permission store（换账号权限串台）**；B-F7 /404 指向登录页；B-F8 FormField 未导入；B-F9 密码框 type="text"（Firefox 明文）。
- seo/log `#expand` 插槽永不渲染 + `type:'number'` 筛选框不渲染；system/log 无意义增删按钮；system/setting 占位页；菜单-视图失配 3 项；keep-alive include 永不匹配；权限按钮未贯穿（自定义 toolbar/actions 绕过）；file.ts 裸 axios。
- **settings/ai「测试连接」端点 `/admin/tag/ai-test` 根本不存在 → 必 404**（比「语义不符」更严重）。

## 4. 新发现（NEW — 上次遗漏）

| # | 位置 | 描述 | 严重级 |
|---|---|---|---|
| N-1 | `logics/file.py:32` | `ALLOWED_IMAGE_TYPES` 含 `image/svg+xml`——即使 uploadImage 走白名单，SVG 本身即存储型 XSS 载体 | HIGH（并入 S3） |
| N-2 | `migrations/015:67-69` + `article.sql:11` | `article_keywords.article_id BIGINT REFERENCES articles.id(INTEGER)`：PG 中 integer→bigint 方向可隐式、反向不行，FK DDL 很可能直接失败——B-D2「必炸」的第二独立触发点 | HIGH |
| N-3 | `init.sql` vs `migrations/` | 两条装库路径互斥（`001` 对已存在 admin_users 无 IF NOT EXISTS），CLAUDE.md 未说明二选一 | GAP |
| N-4 | `seo_simplify.sql` | `TO_REGCLASS` 守卫放 FROM 子句无效，文件不可重跑（只能跑一次） | NIT |
| N-5 | `storage/drivers/s3.py:93` | `endpoint.lstrip('https://')` 用字符集剥离——`http://s3.us-east-1...` 的 `s` 被剥 → host 拼成 `bucket.3.us-east-1...` 必 4xx（`s3.*`/`storage.*` 主机全中招） | HIGH（配置依赖型） |
| N-6 | `views/content/article/index.vue:259` | 调 `/admin/tag/getList` 端点不存在（tag→keyword 迁移后未改）→ 必 404 → tagOptions 恒空 →「按标签生成」功能不可用 | MEDIUM |
| N-7 | `views/settings/ai/index.vue:91` | 同 N-6 同源，tag 模块调用残留 | MEDIUM |
| N-8 | `views/system/user/index.vue:47` | 创建时必填 password、编辑态隐藏、无独立改密入口——admin 端无重置他人密码路径（超管也不行） | GAP |

## 5. 修正后的优先级

| 优先级 | 内容 | 状态 |
|---|---|---|
| **P0** | S1 创建路径提权 + 全站可写字段白名单机制（基础修复：BaseLogic 强制白名单，管理员字段从可写集剔除 is_super_admin/token_version） | 向量收窄仍必改 |
| **P0** | S2 setting 密钥：细分权限 + 凭据脱敏 + 出 Redis 明文缓存 + SecretStr | 维持 |
| **P0** | S3 上传 MIME 白名单 + **SVG 从图片白名单剔除/转安全处理** + size 上限 | 维持+增强 |
| **P0** | S4a/S4b 编辑+batchDelete 归属校验 | 维持 |
| **P0** | S5 登出撤销 refresh + refresh 轮换/重用检测 + client 限流 | 维持 |
| **P1** | 签名/URL bug 三件：COS SHA1、华为 WSSE、**s3 lstrip** | 全部实锤（前两个已获外部规范确认） |
| **P1** | 前端高影响：setQuery、logout 权限串台、404 页、密码框 type、tag/ai-test 404 | 实锤 |
| **P1** | 软删除 CRUD 统一、隐私代理/上传路径基准统一、队列孤儿+重试、权限点种子补齐 | 实锤 |
| **P2** | migrations 016 + 死 SQL 清理 + 015 FK 修正、relationship/FK、菜单-视图对齐、死代码清理、文档同步 | 维持 |
| **P2** | 追加：N-8 用户改密入口、N-2/N-3 建库路径文档化 | 新增 |

## 6. 结论

对抗复核后，**安全核心结论不变**：base 仍存在 2 个半 Critical（S2/S3 全额成立、S1 创建向量成立）+ 一批实锤功能 bug。但**准确性显著提升**：推翻 10 条误报（含 2 个「必炸」级后端结论）、修正 11 条机制/范围/严重级、新增 8 条遗漏（含 2 个 HIGH：s3 lstrip、SVG）。修复优先级以本报告为准。
