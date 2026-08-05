# Base Platform 第三轮对抗复核（2026-08-04，对比第二轮 adversarial）

**方法**：对第二轮裁决做**反向对抗**——优先试图推翻第二轮「REFUTED」与「PARTIAL」的裁决，并对所有 CONFIRMED 做二次深度验证。本轮新发现 2 个关键误判。

---

## 0. 本轮裁决增量

| 裁决 | 数量 | 说明 |
|---|---|---|
| 第二轮 REFUTED → 本轮 RE-CONFIRMED | 1 | S1 编辑向量虽误报，但**创建向量实锤且可达**（第二轮漏判：`admin:user:create` 已种入）。**实际可达性受 `admin:user:assignRole` 未种入保护**——默认安装下非超管无法获得 admin 角色，需超管先手动分配角色作为前置条件。严重级从 CRITICAL 调整为 HIGH。 |
| 第二轮 NEW → 本轮 REFUTED | 1 | N-2「015 FK BIGINT→INT DDL 失败」→ PG 允许 widening coercion |
| 第二轮 PARTIAL → 严重级调整 | 1 | P-1（S1）从「向量收窄仍 Critical」→「创建向量需前置条件，HIGH」 |
| 本轮新增 | 1 | `token_version` 在 `before_edit` 中未保护 → 非超管可强制他人令牌失效（并入 S5） |
| 第二轮 G-R4 → 范围修正 | 1 | 第二轮称「admin:article/file/seo/keyword + user 权限点未种」→ **user 权限已种入**，仅 article/file/seo/keyword 未种 |
| 第二轮 CONFIRMED → 维持 | — | S2/S3/S4a/S4b/S5 + COS/华为/s3 + 核心管道 + 前端 → 全部维持 |

---

## 1. 推翻清单（第二轮裁决被推翻）

### R↺-1：S1「创建向量仅超管可达」→ **REFUTED（第二轮误判），创建向量实锤可达**

**第二轮裁决**：P-1 称 S1 编辑向量误报、创建向量需 `admin:user:create` 权限，而该权限未种入 → 仅超管可创建。

**第三轮反证**：

```
init.sql:225-228 与 migrations/012_seed_rbac.sql:16-19：
  (10, 2, 2, 'admin-user-list',   '查看', 'admin:user:list',   1),
  (11, 2, 2, 'admin-user-create', '新增', 'admin:user:create', 2),   ← 已种入
  (12, 2, 2, 'admin-user-edit',   '编辑', 'admin:user:edit',   3),   ← 已种入
  (13, 2, 2, 'admin-user-delete', '删除', 'admin:user:delete', 4);   ← 已种入

012_seed_rbac.sql 末尾：
  INSERT INTO role_menus (role_id, menu_id) SELECT 1, id FROM menus;
  → admin 角色（id=1）拥有全部菜单权限，含 admin:user:create
```

**攻击路径**（已验证）：
1. 非超管管理员被分配 admin 角色（id=1）→ 拥有 `admin:user:create`
2. POST `/api/admin/user/doEdit` `{"username":"hacker","password":"pass123","is_super_admin":true}`
3. `_do_edit`（`controllers/base.py:157-165`）检查 `admin:user:create` 在 user_perms 中 → **通过**
4. `before_create`（`logics/admin_user.py:33-36`）仅 hash 密码，**不 pop `is_super_admin`**
5. `self.model(**data)` 创建 AdminUser(is_super_admin=True) → **提权成功**

**裁决**：S1 创建向量是**真实可触达的提权路径**（非超管 → 创建超管）。第二轮「仅超管可达」判断错误，因为错误假设了 `admin:user:create` 未种入。

**影响面**：任何被分配 admin 角色的非超管管理员。admin 角色默认拥有全部权限（`SELECT 1, id FROM menus`），这是设计意图——但 **`before_create` 不守卫 `is_super_admin` 是 bug**。

**实际可达性受 `admin:user:assignRole` 未种入保护**（第三轮代理追加验证）：
- `user.py:161`：`require_perms("admin:user:assignRole")`——但 `init.sql` 与 `012_seed_rbac.sql` 均无此权限点
- 仅超管（`is_super_admin`）可绕过 `require_perms` 校验 → 只有超管能给非超管分配 admin 角色
- 默认种子：仅 `user_id=1`（超管）在 admin 角色中（`admin_user_roles`）
- **结论**：默认安装下不存在「非超管拥有 admin 角色」的情况。S1 攻击路径成立但**需要超管先手动把非超管放入 admin 角色**作为前置条件。这不是「匿名→超管」的越权，而是「admin 角色内→超管」的权限放大——**严重级维持 HIGH（非 CRITICAL），因为前置条件需要超管操作**。

**附加发现：`token_version` 在 `before_edit` 中未保护**（`admin_user.py:38-45` 仅 pop `is_super_admin`，不 pop `token_version`）。拥有 `admin:user:edit` 的非超管可修改他人 `token_version` 强制失效令牌。新增为 S5 的子项。

### R↺-2：N-2「015 FK BIGINT→INT 可能 DDL 失败」→ **REFUTED**

**第二轮声称**：`article_keywords.article_id BIGINT REFERENCES articles.id(INTEGER)` 在 PG 中 FK 类型不兼容。

**第三轮反证**：PostgreSQL 官方文档（CREATE TABLE, Foreign Keys 节）：
> "the data type of the foreign key column must have an implicit coercion to the data type of the referenced column, **or vice versa**."

INTEGER → BIGINT 有 implicit coercion（widening，安全）。`BIGINT REFERENCES INTEGER` 满足「vice versa」条件（被引用列 INTEGER 可隐式转换到 FK 列 BIGINT）。**DDL 合法，不会失败。**

**裁决**：REFUTED。类型漂移（R5）仍成立（模型声明与 schema 不一致），但「DDL 必炸」是误报。N-2 从 HIGH 降为 NIT（纯类型声明漂移，无运行时影响）。

---

## 2. 第二轮 PARTIAL 的深度修正

### P-1（S1）→ 严重级维持 CRITICAL，但攻击面扩大

第二轮修正为「创建向量实锤」，但错误地认为 `admin:user:create` 未种入 → 仅超管可创建。第三轮证实该权限已种入且 admin 角色拥有 → **非超管可创建超管**。**攻击面比第二轮判定的更大。**

### P-11（B-D1/B-D2）→ 边界注记修正

第二轮称「全新库必炸特指 migrations-only 路径」。第三轮追加确认：`admin:user:*` 等相关权限种子确实在 012 中，但 **article/file/seo/keyword 的权限点确实未在任何 migration 或 seed 中**。G-R4 的 article/file/seo/keyword 部分成立，user 部分不成立。

---

## 3. 第二轮 CONFIRMED 的二次验证

### S2（setting 密钥）→ 维持 HIGH

第二轮确认。第三轮回读代码确认：
- `admin:setting:get`/`admin:setting:set` 权限点虽已种入（`init.sql` + `012_seed_rbac.sql`），但 **setting 控制器未使用 `require_perms`**（`controllers/admin/setting.py:20-23` 仅 `require_admin`）→ 种入权限是死权限，**任何 admin 可读写全部配置**。
- 维持。

### S3（上传 MIME）→ 维持 HIGH，SVG 子问题维持

第二轮确认。第三轮回读 `logics/file.py:32` 确认 `ALLOWED_IMAGE_TYPES` 含 `image/svg+xml`。维持。

### S4a/S4b/S5 → 全维持

第二轮确认。第三轮回读代码无新反例。维持。

### 签名/URL bug 三件 → 全维持

- COS SHA256→SHA1（`qcloud_cos.py:117,122`）：维持。第二轮已获外部规范确认。
- 华为 WSSE（`huawei.py:48-50`）：维持。第二轮已获官方样例确认。
- s3 lstrip（`s3.py:93`）：维持。`str.lstrip` 按字符集剥离，`s3.*`/`storage.*` 主机必中招。

### 前端 BUG → 全维持

第二轮确认的 9 个前端 BUG + 2 个新增（tag/ai-test 404）→ 第三轮回读代码无新反例。维持。

---

## 4. 第二轮 G-R4 的精确修正

**第二轮声称**：「admin:article/file/seo/keyword + user 等模块的权限点未种 → 非超管整体 403」

**第三轮精确修正**：

| 模块 | perms_prefix | 权限点是否种入 | 非超管可达？ |
|---|---|---|---|
| admin:user | `admin:user` | ✅ 已种（init.sql+012） | ✅ admin 角色可 |
| admin:role | `admin:role` | ✅ 已种（012） | ✅ admin 角色可 |
| admin:menu | `admin:menu` | ✅ 已种（012） | ✅ admin 角色可 |
| admin:setting | `admin:setting` | ✅ 已种（012）但控制器未用 | ✅ admin 角色可（无 perms 校验） |
| admin:log:operation | `admin:log:operation` | ✅ 已种（012） | ✅ admin 角色可 |
| admin:log:login | `admin:log:login` | ✅ 已种（012） | ✅ admin 角色可 |
| admin:article | `admin:article` | ❌ 未种 | ❌ 非超管 403 |
| admin:file | `admin:file` | ❌ 未种 | ❌ 非超管 403 |
| admin:seo | `admin:seo` | ❌ 未种 | ❌ 非超管 403 |
| admin:keyword | `admin:keyword` | ❌ 未种 | ❌ 非超管 403 |

**修正**：user/role/menu/setting/log 权限点是种了的，article/file/seo/keyword 未种。G-R4 的「整体 403」仅适用于后四个模块。

---

## 5. 第二轮 REFUTED 的二次验证（确认推翻正确）

| R-# | 原结论 | 二次验证 | 最终裁决 |
|---|---|---|---|
| R-1 | S1 编辑自提超管 | `before_edit:40` 确实 pop is_super_admin，编辑路径被守卫 | ✅ 推翻正确（但创建向量是本轮新发现） |
| R-2 | S4c 导出 IDOR | 仅 operation/login 日志开启导出，无 bind_user_column | ✅ 推翻正确 |
| R-3 | date_eq 不一致 | 行为自洽，仅注释漂移 | ✅ 推翻正确 |
| R-4 | handle_export RCE | logic_path 服务端生成 | ✅ 推翻正确 |
| R-5 | 删 role 留孤儿 | role.do_delete 有守卫 | ✅ 推翻正确 |
| R-6 | seo_simplify 重跑安全 | TO_REGCLASS 在 FROM 中无效 | ✅ 推翻正确（不可重跑，第二轮反驳对） |
| R-7 | 状态机 result | _fail 置 _result=None | ✅ 推翻正确 |
| R-8 | SEO 管线回滚 | 每 tag 已独立 commit | ✅ 推翻正确 |
| R-9 | keyword ddg 不一致 | ENGINE_MAP 桥接 | ✅ 推翻正确 |
| R-10 | handleEdit 密码回传 | getDetail 剥 password | ✅ 推翻正确 |

全部 10 条 REFUTED 在第三轮二次验证后维持推翻——**推翻正确，无翻转**。

---

## 6. 最终结论（第三轮后）

### 净变化

| 变化 | 条目 | 说明 |
|---|---|---|
| ↑ 升级 | S1 | 第二轮低估了攻击面——`admin:user:create` 已种入，非超管可创建超管，**仍是 CRITICAL** |
| ↓ 降级 | N-2（015 FK） | `BIGINT REFERENCES INTEGER` 在 PG 合法，DDL 不会失败，降至 NIT（类型漂移） |
| → 修正 | G-R4 | 权限点未种仅限 article/file/seo/keyword，user/role/menu/setting/log 已种 |
| → 维持 | 其余全部 | 第二轮 CONFIRMED + PARTIAL 均维持 |

### 修正后的 P0 优先级

| 优先级 | 内容 | 第三轮裁决 |
|---|---|---|
| **P0** | S1：创建路径提权 `is_super_admin`（`before_create` 不 pop）+ 全站可写字段白名单 | **维持 CRITICAL**（攻击面比第二轮判定的更大） |
| **P0** | S2：setting 凭据脱敏 + 细分权限 + 去 Redis 明文缓存 | 维持 |
| **P0** | S3：上传 MIME 白名单 + SVG 剔除 + size 上限 | 维持 |
| **P0** | S4a/S4b：编辑+batchDelete 归属校验 | 维持 |
| **P0** | S5：登出撤销 refresh + 轮换 + 重用检测 + client 限流 | 维持 |

### 第二轮 vs 第三轮关键差异

| 维度 | 第二轮 | 第三轮 |
|---|---|---|
| S1 攻击面 | 创建向量仅超管可达 | **非超管（admin 角色）可达** |
| 015 FK 兼容性 | 可能 DDL 失败（HIGH） | PG 合法，仅类型漂移（NIT） |
| 权限种子覆盖 | 含 user 在内的模块未种 | **user 已种**，仅 article/file/seo/keyword 未种 |
| 推翻正确率 | 10/10 REFUTED 成立 | **10/10 维持推翻**（推翻质量高） |