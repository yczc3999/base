# RBAC 权限系统设计文档

## 一、设计目标

为基础平台提供完整的角色权限管理能力：
- 管理员可以有多个角色，角色可以分配菜单和按钮权限
- 前端根据角色渲染菜单树 + 按钮权限
- 后端根据权限标识拦截无权操作
- 超级管理员（is_super_admin=true）拥有全部权限，不受限制
- 支持用户名或邮箱登录
- RBAC 仅作用于 admin 端，client 端不涉及角色权限

---

## 二、方案选型

**菜单与权限分离，两级菜单**

菜单（menus）管 UI 渲染，权限（permissions）管后端拦截，各司其职。
菜单只有两级：目录 → 页面。按钮权限挂在菜单上，不占菜单层级。

关系：
```
admin_users ←→ admin_user_roles ←→ roles ←→ role_menus ←→ menus
                                         ←→ role_permissions ←→ permissions
```

简化版（权限挂在菜单上，不单独建 permissions 表）：
```
admin_users ←→ admin_user_roles ←→ roles ←→ role_menus ←→ menus
                                                              └→ menu_permissions（菜单下的按钮权限，存在 menus 表中 type=2）
```

**最终方案：权限作为 menus 表的 type=2 记录**
- 不单独建 permissions 表
- 按钮权限是 menus 表中 type=2 的记录，parent_id 指向所属菜单
- 前端只渲染 type=0 和 type=1，type=2 仅用于权限判断
- 两级菜单 + 按钮权限 = 结构清晰，够用不过度

---

## 三、数据库设计

### 3.1 menus — 菜单表（两级菜单 + 按钮权限）

```
第 1 级：目录（type=0）— 系统管理、内容管理...
第 2 级：页面（type=1）— 用户管理、角色管理...
附属：  按钮（type=2）— 挂在页面下，不占菜单层级
```

| 字段 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| id | INT | PK | 自增 | 主键 |
| parent_id | INT | Y | 0 | 父级 ID（0=顶级目录） |
| type | SMALLINT | Y | 0 | 0=目录 1=菜单页面 2=按钮权限 |
| slug | VARCHAR(50) | Y | | 唯一标识（英文，URL 友好，如 "admin-user"、"system"） |
| label | VARCHAR(50) | Y | | 显示名称（如 "用户管理"、"系统管理"） |
| icon | VARCHAR(100) | | | 图标标识（如 "Users"、"Settings"、"FileText"） |
| path | VARCHAR(200) | | | 前端路由路径（如 "/system/user"） |
| template_path | VARCHAR(200) | | | 前端组件路径（如 "system/user/index"） |
| redirect | VARCHAR(200) | | | 重定向地址（目录类型，如 "/system/user"） |
| perms | VARCHAR(100) | | | 权限标识（按钮类型，如 "admin:user:create"） |
| link | VARCHAR(500) | | | 外链地址（如 "https://docs.example.com"） |
| link_target | VARCHAR(10) | | _self | 外链打开方式：_self / _blank |
| is_cache | BOOLEAN | Y | true | 页面是否缓存（keep-alive） |
| is_affix | BOOLEAN | Y | false | 是否固定在标签栏（如首页） |
| is_visible | BOOLEAN | Y | true | 是否在菜单中显示 |
| badge | VARCHAR(20) | | | 徽标（如 "NEW"、"HOT"、数字） |
| sort | INT | Y | 0 | 排序（升序，数字越小越靠前） |
| status | SMALLINT | Y | 1 | 0=禁用 1=正常 |
| remark | VARCHAR(255) | | | 备注 |
| created_at | TIMESTAMP | Y | now() | |
| updated_at | TIMESTAMP | Y | now() | |

**索引：**
- PRIMARY KEY (id)
- UNIQUE (slug)
- INDEX (parent_id)
- INDEX (sort)

**字段用途矩阵：**

| 字段 | 目录(type=0) | 菜单(type=1) | 按钮(type=2) |
|------|:-----------:|:-----------:|:-----------:|
| slug | ✅ | ✅ | ✅ |
| label | ✅ | ✅ | ✅ |
| icon | ✅ | ✅ | — |
| path | — | ✅ | — |
| template_path | — | ✅ | — |
| redirect | ✅ 可选 | — | — |
| perms | — | — | ✅ |
| link | — | ✅ 可选 | — |
| link_target | — | ✅ 可选 | — |
| is_cache | — | ✅ | — |
| is_affix | — | ✅ | — |
| is_visible | ✅ | ✅ | — |
| badge | ✅ | ✅ | — |
| sort | ✅ | ✅ | ✅ |

### 3.2 roles — 角色表

| 字段 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| id | INT | PK | 自增 | 主键 |
| name | VARCHAR(50) | Y | | 角色标识（英文，唯一，如 "admin"、"editor"） |
| label | VARCHAR(50) | Y | | 显示名（如 "管理员"、"编辑员"） |
| remark | VARCHAR(255) | | | 备注 |
| sort | INT | Y | 0 | 排序 |
| status | SMALLINT | Y | 1 | 0=禁用 1=正常 |
| created_at | TIMESTAMP | Y | now() | |
| updated_at | TIMESTAMP | Y | now() | |

**索引：**
- PRIMARY KEY (id)
- UNIQUE (name)

### 3.3 role_menus — 角色菜单关联表

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| role_id | INT | Y | 角色 ID |
| menu_id | INT | Y | 菜单 ID（包括 type=0/1/2） |

**索引：**
- PRIMARY KEY (role_id, menu_id)

### 3.4 admin_user_roles — 管理员角色关联表

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| admin_user_id | INT | Y | 管理员 ID |
| role_id | INT | Y | 角色 ID |

**索引：**
- PRIMARY KEY (admin_user_id, role_id)

### 3.5 admin_users 表改动

- 不新增字段，`is_super_admin` 保留
- `email` 字段需新增唯一索引（支持邮箱登录）
- 用户名（alpha_num 规则）不允许包含 `@`，与邮箱天然区分

---

## 四、权限标识规则

### 4.1 格式

```
{端}:{模块}:{操作}
```

### 4.2 CRUD 标准操作

| 操作 | 权限标识 | 对应接口 | 判断方式 |
|------|---------|---------|---------|
| 查看列表 | admin:user:list | GET /getList | — |
| 查看详情 | admin:user:detail | GET /getDetail | — |
| 新增 | admin:user:create | POST /doEdit（无 id） | 请求体无主键 |
| 编辑 | admin:user:edit | POST /doEdit（有 id） | 请求体有主键 |
| 删除 | admin:user:delete | POST /doDelete | — |

### 4.3 特殊操作

| 权限标识 | 说明 |
|---------|------|
| admin:user:resetPassword | 重置密码 |
| admin:user:assignRole | 分配角色 |
| admin:role:assignMenu | 分配权限 |
| admin:setting:set | 修改配置 |

---

## 五、鉴权流程

### 5.1 后端鉴权

```
请求进来
  │
  ├→ Depends(require_admin)                       # 第一层：验证登录
  │     └→ 从 Redis token 读取 user_info
  │
  ├→ Depends(require_perms("admin:user:list"))    # 第二层：验证权限
  │     ├→ is_super_admin = True → 直接放行
  │     ├→ 查 Redis 缓存的用户权限列表
  │     │     └→ 缓存未命中 → 查 DB：user → roles → role_menus → menus(type=2).perms
  │     ├→ 权限列表取并集（多角色，有一个角色有权限就放行）
  │     ├→ 匹配 perms → 放行
  │     └→ 不匹配 → 403 无权限
  │
  └→ 执行业务逻辑
```

### 5.2 权限缓存策略

```
Redis Key:  {APP_NAME}:user_perms:{user_id}
Value:      ["admin:user:list", "admin:user:create", ...]
TTL:        与 access_token 同生命周期
```

**缓存清除时机：**
- 角色权限变更（修改 role_menus）→ 查该角色下所有用户 → 逐个清除
- 用户角色变更（修改 admin_user_roles）→ 清除该用户缓存
- 用户登出 → 随 token 一起过期

### 5.3 前端菜单获取

```
GET /api/admin/user/menus
```

**流程：**
1. 超级管理员 → 返回全部 status=1 的菜单
2. 普通管理员 → 查用户角色 → 查角色菜单 → 合并去重 → 过滤 status=1
3. 分离：type=0/1 → 构建菜单树；type=2 → 提取 perms 数组
4. 菜单树按 sort 排序

**响应格式：**
```json
{
  "code": 0,
  "data": {
    "menus": [
      {
        "id": 1,
        "slug": "system",
        "label": "系统管理",
        "icon": "Settings",
        "type": 0,
        "redirect": "/system/user",
        "children": [
          {
            "id": 2,
            "slug": "admin-user",
            "label": "用户管理",
            "icon": "Users",
            "type": 1,
            "path": "/system/user",
            "template_path": "system/user/index",
            "is_cache": true,
            "is_affix": false
          },
          {
            "id": 3,
            "slug": "role",
            "label": "角色管理",
            "icon": "Shield",
            "type": 1,
            "path": "/system/role",
            "template_path": "system/role/index"
          }
        ]
      }
    ],
    "permissions": [
      "admin:user:list",
      "admin:user:create",
      "admin:user:edit",
      "admin:user:delete",
      "admin:role:list"
    ]
  }
}
```

---

## 六、登录方式

### 6.1 用户名 / 邮箱登录

登录接口的 `username` 字段同时支持用户名和邮箱：

```python
if "@" in login_name:
    user = await self.get_by_field(db, "email", login_name)
else:
    user = await self.get_by_field(db, "username", login_name)
```

### 6.2 约束

- `email` 字段加唯一索引
- `username` 校验规则为 `alpha_num`（不允许 `@`），与邮箱天然区分
- 邮箱登录走 `email` 字段缓存，用户名登录走 `username` 字段缓存

---

## 七、crud_router 权限集成

### 7.1 自动权限

```python
crud_router("user", admin_user_logic,
    auth_dep=require_admin,
    perms_prefix="admin:user",
)
```

| 接口 | 权限 | 判断方式 |
|------|------|---------|
| getList | admin:user:list | — |
| getDetail | admin:user:detail | — |
| doEdit（无 id） | admin:user:create | 请求体无主键 |
| doEdit（有 id） | admin:user:edit | 请求体有主键 |
| doDelete | admin:user:delete | — |

### 7.2 手动权限

```python
@router.post("/user/resetPassword")
async def reset_password(
    auth: AuthInfo = Depends(require_admin),
    _: None = Depends(require_perms("admin:user:resetPassword")),
):
    ...
```

---

## 八、初始数据

### 8.1 默认角色

| name | label | 说明 |
|------|-------|------|
| admin | 管理员 | 分配全部菜单权限 |
| editor | 编辑员 | 示例角色，按需分配 |

### 8.2 默认菜单树

```
系统管理 (type=0, slug=system, icon=Settings)
├── 用户管理 (type=1, slug=admin-user, icon=Users, path=/system/user)
│   ├── 查看 (type=2, slug=admin-user-list, perms=admin:user:list)
│   ├── 新增 (type=2, slug=admin-user-create, perms=admin:user:create)
│   ├── 编辑 (type=2, slug=admin-user-edit, perms=admin:user:edit)
│   └── 删除 (type=2, slug=admin-user-delete, perms=admin:user:delete)
├── 角色管理 (type=1, slug=role, icon=Shield, path=/system/role)
│   ├── 查看 (type=2, slug=role-list, perms=admin:role:list)
│   ├── 新增 (type=2, slug=role-create, perms=admin:role:create)
│   ├── 编辑 (type=2, slug=role-edit, perms=admin:role:edit)
│   ├── 删除 (type=2, slug=role-delete, perms=admin:role:delete)
│   └── 分配权限 (type=2, slug=role-assign, perms=admin:role:assignMenu)
├── 菜单管理 (type=1, slug=menu, icon=Menu, path=/system/menu)
│   ├── 查看 (type=2, slug=menu-list, perms=admin:menu:list)
│   ├── 新增 (type=2, slug=menu-create, perms=admin:menu:create)
│   ├── 编辑 (type=2, slug=menu-edit, perms=admin:menu:edit)
│   └── 删除 (type=2, slug=menu-delete, perms=admin:menu:delete)
├── 系统配置 (type=1, slug=setting, icon=Sliders, path=/system/setting)
│   ├── 查看 (type=2, slug=setting-get, perms=admin:setting:get)
│   └── 修改 (type=2, slug=setting-set, perms=admin:setting:set)
└── 日志管理 (type=0, slug=log, icon=FileText)
    ├── 操作日志 (type=1, slug=operation-log, icon=Activity, path=/system/log/operation)
    │   └── 查看 (type=2, slug=operation-log-list, perms=admin:log:operation:list)
    └── 登录日志 (type=1, slug=login-log, icon=LogIn, path=/system/log/login)
        └── 查看 (type=2, slug=login-log-list, perms=admin:log:login:list)
```

### 8.3 超级管理员

- `is_super_admin = true` 直接返回完整菜单树 + 全部 perms
- 后端鉴权直接放行，不查 role_menus

---

## 九、安全考虑

| # | 风险 | 措施 |
|---|------|------|
| 1 | 权限缓存不一致 | 修改角色权限后，清除该角色下所有用户的权限缓存 |
| 2 | 菜单误删 | 有子菜单的不允许删除（before_delete 校验） |
| 3 | 角色误删 | 有用户关联的角色不允许删除（before_delete 校验） |
| 4 | 超级管理员降级 | is_super_admin 不能通过接口修改（before_edit 过滤） |
| 5 | 前端绕过 | 前端按钮权限只是 UI 控制，后端 require_perms 做最终校验 |
| 6 | 邮箱重复 | email 加唯一索引，注册/编辑时校验 |
| 7 | 菜单层级越界 | before_create 校验 parent 层级，目录下只能建页面，页面下只能建按钮 |

---

## 十、影响范围

### 10.1 新增文件

| 文件 | 说明 |
|------|------|
| `models/menu.py` | Menu Model（含 Type 枚举） |
| `models/role.py` | Role Model（含 Status 枚举） |
| `models/role_menu.py` | RoleMenu 关联 Model |
| `models/admin_user_role.py` | AdminUserRole 关联 Model |
| `logics/menu.py` | 菜单 Logic（CRUD + 树构建 + 层级校验 + 删除保护） |
| `logics/role.py` | 角色 Logic（CRUD + 权限分配 + 缓存清除 + 删除保护） |
| `controllers/admin/menu.py` | 菜单管理路由 |
| `controllers/admin/role.py` | 角色管理路由 |
| `databases/migrations/007_create_menus.sql` | 菜单表 |
| `databases/migrations/008_create_roles.sql` | 角色表 |
| `databases/migrations/009_create_role_menus.sql` | 角色菜单关联表 |
| `databases/migrations/010_create_admin_user_roles.sql` | 管理员角色关联表 |
| `databases/migrations/011_add_email_unique_index.sql` | admin_users email 唯一索引 |
| `databases/migrations/012_seed_rbac.sql` | RBAC 初始数据 |

### 10.2 修改文件

| 文件 | 改动 |
|------|------|
| `models/__init__.py` | 导出新 Model |
| `deps.py` | 新增 `require_perms()` 依赖 |
| `controllers/admin/user.py` | 登录支持邮箱 + 新增 /user/menus 接口 |
| `controllers/base.py` | crud_router 新增 perms_prefix 参数 |
| `logics/admin_user.py` | verify_login 支持邮箱、cache_fields 增加 email |
| `main.py` | 注册菜单和角色路由 |
