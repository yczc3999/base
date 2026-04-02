# RBAC 实现规划 — Model / Logic / Controller

> 基于 rbac-design.md 的设计，逐层规划每个文件的字段、方法、接口。

---

## 一、Model 层

### 1.1 models/menu.py — Menu

```python
class Menu(Base):
    __tablename__ = "menus"

    class Type(IntEnum):
        DIRECTORY = 0   # 目录
        MENU = 1        # 菜单页面
        BUTTON = 2      # 按钮权限

    class Status(IntEnum):
        DISABLED = 0
        ACTIVE = 1

    id            # INT PK
    parent_id     # INT DEFAULT 0
    type          # SMALLINT DEFAULT 0
    slug          # VARCHAR(50) UNIQUE NOT NULL
    label         # VARCHAR(50) NOT NULL
    icon          # VARCHAR(100) NULL
    path          # VARCHAR(200) NULL      — 前端路由
    template_path # VARCHAR(200) NULL      — 前端组件路径
    redirect      # VARCHAR(200) NULL      — 目录重定向
    perms         # VARCHAR(100) NULL      — 权限标识
    link          # VARCHAR(500) NULL      — 外链
    link_target   # VARCHAR(10) DEFAULT '_self'
    is_cache      # BOOLEAN DEFAULT TRUE   — keep-alive
    is_affix      # BOOLEAN DEFAULT FALSE  — 标签栏固定
    is_visible    # BOOLEAN DEFAULT TRUE   — 菜单显示
    badge         # VARCHAR(20) NULL       — 徽标
    sort          # INT DEFAULT 0
    status        # SMALLINT DEFAULT 1
    remark        # VARCHAR(255) NULL
    created_at    # TIMESTAMP DEFAULT now()
    updated_at    # TIMESTAMP DEFAULT now()
```

### 1.2 models/role.py — Role

```python
class Role(Base):
    __tablename__ = "roles"

    class Status(IntEnum):
        DISABLED = 0
        ACTIVE = 1

    id            # INT PK
    name          # VARCHAR(50) UNIQUE NOT NULL  — 标识（如 admin）
    label         # VARCHAR(50) NOT NULL         — 显示名
    remark        # VARCHAR(255) NULL
    sort          # INT DEFAULT 0
    status        # SMALLINT DEFAULT 1
    created_at    # TIMESTAMP DEFAULT now()
    updated_at    # TIMESTAMP DEFAULT now()
```

### 1.3 models/role_menu.py — RoleMenu

```python
class RoleMenu(Base):
    __tablename__ = "role_menus"

    role_id       # INT NOT NULL  — 联合主键
    menu_id       # INT NOT NULL  — 联合主键
```

无 created_at / updated_at，纯关联表。

### 1.4 models/admin_user_role.py — AdminUserRole

```python
class AdminUserRole(Base):
    __tablename__ = "admin_user_roles"

    admin_user_id  # INT NOT NULL  — 联合主键
    role_id        # INT NOT NULL  — 联合主键
```

纯关联表。

### 1.5 models/__init__.py 更新

新增导出：Menu, Role, RoleMenu, AdminUserRole

---

## 二、Logic 层

### 2.1 logics/menu.py — MenuLogic

**类属性：**
```python
class MenuLogic(BaseLogic):
    model = Menu
    cache_prefix = "menu"
    except_keys = []
    create_rules = {
        "slug": "required|min:2|max:50|alpha_num",  # 允许连字符？用 regex
        "label": "required|max:50",
        "type": "required|in:0,1,2",
    }
    edit_rules = {
        "slug": "min:2|max:50",
        "label": "max:50",
    }
```

**白名单：**
```python
def allowed_filters(self):
    return ["id", "parent_id", "type", "slug", "label", "status", "is_visible"]

def allowed_sorts(self):
    return ["id", "sort", "created_at"]

def keyword_fields(self):
    return ["slug", "label", "perms"]
```

**自定义方法：**

| 方法 | 说明 |
|------|------|
| `get_tree(db) → list` | 查全部 status=1 的菜单，用 to_tree 构建树，按 sort 排序 |
| `get_tree_by_role_ids(db, role_ids) → list` | 查指定角色的菜单树（去重合并） |
| `get_perms_by_role_ids(db, role_ids) → list[str]` | 查指定角色的权限标识列表（type=2 的 perms） |
| `get_all_perms(db) → list[str]` | 查全部权限标识（超级管理员用） |

**钩子：**

| 钩子 | 逻辑 |
|------|------|
| `before_create(data)` | 校验层级：type=0 的 parent_id 必须是 0；type=1 的 parent 必须是 type=0；type=2 的 parent 必须是 type=1 |
| `before_delete(pk_value)` | 校验子菜单：有子记录不允许删除 |

### 2.2 logics/role.py — RoleLogic

**类属性：**
```python
class RoleLogic(BaseLogic):
    model = Role
    cache_prefix = "role"
    create_rules = {
        "name": "required|min:2|max:50|alpha_num",
        "label": "required|max:50",
    }
    edit_rules = {
        "name": "min:2|max:50|alpha_num",
        "label": "max:50",
    }
```

**白名单：**
```python
def allowed_filters(self):
    return ["id", "name", "label", "status"]

def allowed_sorts(self):
    return ["id", "sort", "created_at"]

def keyword_fields(self):
    return ["name", "label"]
```

**自定义方法：**

| 方法 | 说明 |
|------|------|
| `assign_menus(db, role_id, menu_ids)` | 分配菜单权限：先删旧的 role_menus，再批量插入新的，最后清除该角色下所有用户的权限缓存 |
| `get_menu_ids(db, role_id) → list[int]` | 获取角色已分配的菜单 ID 列表 |
| `get_roles_by_user(db, user_id) → list[dict]` | 获取用户的角色列表 |
| `_clear_role_perms_cache(db, role_id)` | 查该角色下所有用户，清除每个用户的权限缓存 |

**钩子：**

| 钩子 | 逻辑 |
|------|------|
| `before_delete(pk_value)` | 校验关联：有 admin_user_roles 记录不允许删除 |
| `after_delete(pk_value)` | 清除 role_menus 关联记录 |

### 2.3 logics/admin_user.py — 修改

**新增方法：**

| 方法 | 说明 |
|------|------|
| `verify_login(db, login_name, password)` | **修改**：login_name 包含 @ 按 email 查，否则按 username 查 |
| `assign_roles(db, user_id, role_ids)` | 分配角色：先删旧的 admin_user_roles，再批量插入，清除用户权限缓存 |
| `get_role_ids(db, user_id) → list[int]` | 获取用户已分配的角色 ID 列表 |
| `get_user_perms(db, user_id) → list[str]` | 获取用户权限列表（走 Redis 缓存） |
| `get_user_menus(db, user_id, is_super) → dict` | 获取用户菜单树 + 权限列表 |

**修改：**
```python
cache_fields = ["username", "email"]  # 新增 email 缓存
```

### 2.4 deps.py — 新增 require_perms

```python
async def require_perms(*perms: str):
    """
    权限校验依赖

    用法：
        Depends(require_perms("admin:user:list"))
        Depends(require_perms("admin:user:create", "admin:user:edit"))  # 满足任一即可

    流程：
        1. is_super_admin → 直接放行
        2. Redis 缓存查用户权限列表
        3. 缓存未命中 → DB 查询 → 写缓存
        4. 匹配 perms → 放行
        5. 不匹配 → 403
    """
```

返回一个 FastAPI Depends 可用的依赖函数。

---

## 三、Controller 层

### 3.1 controllers/admin/menu.py — 菜单管理

**CRUD（通过 crud_router 自动生成）：**

```python
crud_router("menu", menu_logic,
    auth_dep=require_admin,
    perms_prefix="admin:menu",
)
```

自动生成：
- `GET /api/admin/menu/getList` — 菜单列表（扁平，支持 filters）
- `GET /api/admin/menu/getDetail` — 菜单详情
- `POST /api/admin/menu/doEdit` — 创建/编辑菜单
- `POST /api/admin/menu/doDelete` — 删除菜单

**自定义接口：**

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| GET | /menu/tree | require_admin | 获取完整菜单树（管理菜单用，不按角色过滤） |

### 3.2 controllers/admin/role.py — 角色管理

**CRUD（通过 crud_router 自动生成）：**

```python
crud_router("role", role_logic,
    auth_dep=require_admin,
    perms_prefix="admin:role",
)
```

**自定义接口：**

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| GET | /role/menuIds | require_admin + admin:role:list | 获取角色已分配的菜单 ID 列表 |
| POST | /role/assignMenus | require_admin + admin:role:assignMenu | 分配菜单权限 |

**assignMenus 请求体：**
```json
{
  "role_id": 1,
  "menu_ids": [1, 2, 3, 10, 11, 12]
}
```

### 3.3 controllers/admin/user.py — 修改

**新增接口：**

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| GET | /user/menus | require_admin | 获取当前用户的菜单树 + 权限列表 |
| GET | /user/roleIds | require_admin + admin:user:list | 获取指定用户的角色 ID 列表 |
| POST | /user/assignRoles | require_admin + admin:user:assignRole | 给用户分配角色 |

**修改接口：**
- `POST /user/login` — 支持邮箱登录（包含 @ 走 email 查询）
- 登录成功响应新增 `permissions` 字段

**assignRoles 请求体：**
```json
{
  "user_id": 2,
  "role_ids": [1, 2]
}
```

**登录响应变更：**
```json
{
  "code": 0,
  "data": {
    "access_token": "...",
    "refresh_token": "...",
    "expires_in": 7200,
    "user": {
      "id": 1,
      "username": "admin",
      "nickname": "超级管理员",
      "is_super_admin": true,
      "roles": ["admin"],
      "...": "..."
    }
  }
}
```

### 3.4 controllers/base.py — crud_router 修改

新增 `perms_prefix` 参数：

```python
def crud_router(
    prefix: str,
    logic: BaseLogic,
    tags: list[str] = None,
    need_auth: bool = True,
    no_auth: list[str] = None,
    auth_dep=None,
    perms_prefix: str = "",     # 新增：权限前缀
    actions: dict = None,
) -> APIRouter:
```

当 `perms_prefix` 非空时，自动为每个 CRUD 方法注入权限校验：
- getList → `require_perms("{perms_prefix}:list")`
- getDetail → `require_perms("{perms_prefix}:detail")`
- doEdit 无 id → `require_perms("{perms_prefix}:create")`
- doEdit 有 id → `require_perms("{perms_prefix}:edit")`
- doDelete → `require_perms("{perms_prefix}:delete")`

`perms_prefix` 为空时行为不变（不校验权限，只校验登录）。

---

## 四、main.py 注册

```python
from app.controllers.admin import menu as admin_menu
from app.controllers.admin import role as admin_role

app.include_router(admin_menu.router, prefix="/api/admin")
app.include_router(admin_role.router, prefix="/api/admin")
```

---

## 五、迁移文件

| 文件 | 说明 |
|------|------|
| `007_create_menus.sql` | 建表 + 索引 + COMMENT |
| `008_create_roles.sql` | 建表 + 索引 + COMMENT |
| `009_create_role_menus.sql` | 建表 + 联合主键 |
| `010_create_admin_user_roles.sql` | 建表 + 联合主键 |
| `011_add_email_unique_index.sql` | admin_users.email 加唯一索引 |
| `012_seed_rbac.sql` | 插入默认菜单树 + 默认角色 + 角色菜单关联 |

---

## 六、实现顺序

```
1. 迁移文件（建表）
2. Model 层（4 个文件 + __init__ 更新）
3. Logic 层（menu.py + role.py + admin_user.py 修改）
4. deps.py（require_perms）
5. Controller 层（menu.py + role.py + user.py 修改 + base.py 修改）
6. main.py（注册路由）
7. 测试
8. 更新 ASSETS.md
```
