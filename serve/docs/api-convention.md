# 前后端 CRUD 接口约定文档

本文档定义了前端与后端交互的 **CRUD 接口规范**，包括请求格式、响应格式、Filters 查询 DSL 和约束条件。  
所有通过集中式 Route Registry 的 `RouteGroup.crud()` 生成的接口均遵守此约定；`controllers.base.crud_router()` 仅作为下游兼容层保留。

---

## 一、统一响应格式

所有接口返回 **HTTP 200**，通过 `code` 字段区分业务状态：

```json
{
  "code": 0,        // 0=成功，非0=失败
  "msg": "success", // 提示信息
  "data": {}        // 业务数据，失败时为 null
}
```

### 错误码约定

| code | 含义 | 场景 |
|------|------|------|
| 0 | 成功 | 所有正常响应 |
| 1 | 通用失败 | 参数错误、业务逻辑失败 |
| 400 | 业务异常 | BizError 抛出 |
| 401 | 未认证 | 未登录、token 过期/失效 |
| 403 | 无权限 | 账号禁用 |
| 500 | 服务器错误 | 未捕获的异常 |

---

## 二、认证

除标记为公开的接口外，所有请求需在 Header 中携带 JWT Token：

```
Authorization: Bearer <token>
```

认证流程：
1. JWT 解析 → 提取 `userId` / `scope` / `tokenVersion`
2. Redis 缓存校验 `tokenVersion`（命中跳过 DB）
3. 缓存未命中 → 查 DB → 写入 Redis 缓存

Token 过期或版本号不匹配时返回：

```json
{ "code": 401, "msg": "请登录", "data": null }
```

---

## 三、CRUD 五个标准接口

每个通过 `RouteGroup.crud()` 注册的模块自动拥有以下 5 个接口：

### 3.1 获取列表 — `GET /{module}/getList`

**请求参数（Query）：**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| page | number | 否 | 1 | 当前页码（最小 1） |
| pageSize | number | 否 | 20 | 每页条数（范围 1-100） |
| sortField | string | 否 | id | 排序字段（受 `allowed_sorts()` 白名单约束） |
| sortOrder | string | 否 | desc | 排序方向：asc / desc |
| filters | string(JSON) | 否 | {} | 筛选条件 DSL（详见第四章） |
| keyword | string | 否 | "" | 关键词搜索（搜索字段由 `keyword_fields()` 决定） |

**响应：**

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "list": [
      { "id": 1, "username": "admin", "nickname": "超级管理员", "status": 1, ... }
    ],
    "total": 50,
    "page": 1,
    "pageSize": 20
  }
}
```

**约束：**
- `pageSize` 范围 1-100，超出自动钳位
- `sortField` 不在白名单内时回退为主键
- `filters` 中的字段受 `allowed_filters()` 白名单约束，非法字段被静默忽略
- 响应中的 `list` 已过滤敏感字段（如 password）

---

### 3.2 获取详情 — `GET /{module}/getDetail`

**请求参数（Query）：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| id | number | 是 | 主键值 |

**响应：**

```json
{
  "code": 0,
  "msg": "success",
  "data": { "id": 1, "username": "admin", "nickname": "超级管理员", ... }
}
```

**约束：**
- 主键参数不能为空，否则返回 `{ "code": 1, "msg": "缺少主键参数" }`
- 非数字主键返回 `{ "code": 1, "msg": "主键参数格式错误" }`
- 记录不存在时返回 `{ "code": 1, "msg": "数据不存在" }`
- 优先查 Redis 缓存，缓存未命中查 DB 并回填缓存

---

### 3.3 创建或编辑 — `POST /{module}/doEdit`

**请求体（JSON）：**

- **创建**：不传主键字段
- **编辑**：传主键字段

**创建示例：**

```json
POST /api/admin/user/doEdit
{
  "username": "zhangsan",
  "password": "123456",
  "nickname": "张三"
}
```

**编辑示例：**

```json
POST /api/admin/user/doEdit
{
  "id": 2,
  "nickname": "张三丰",
  "status": 0
}
```

**约束：**
- 创建和编辑共用一个接口，**有主键 = 编辑，无主键 = 创建**
- 数据入库前经过 `format_save_data()` 格式化（空字符串字段被移除）
- 创建前触发 `before_create()` 钩子
- 编辑前触发 `before_edit()` 钩子
- 编辑时先清旧缓存，再更新 DB
- 业务异常（`BizError`）会被捕获并返回 `{ "code": 400, "msg": "..." }`

---

### 3.4 删除 — `POST /{module}/doDelete`

**请求体（JSON）：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| ids | int / int[] / string | 是 | 要删除的主键，支持多种格式 |

**支持的 ids 格式：**

```json
// 格式一：逗号分隔字符串
{ "ids": "1,2,3" }

// 格式二：数组
{ "ids": [1, 2, 3] }

// 格式三：单个值
{ "ids": 5 }
```

**约束：**
- `ids` 不能为空，否则返回 `{ "code": 1, "msg": "缺少 ids 参数" }`
- 逐条删除，每条都会触发 `before_delete()` / `after_delete()` 钩子
- 如果 Model 有 `deleted_at` 字段，自动走软删除（更新 `deleted_at`，不物理删除）
- 如果 Model 没有 `deleted_at` 字段，物理删除

---

## 四、Filters 查询 DSL

`filters` 是 `getList` 接口的核心查询参数，支持 **19 个操作符** + **逻辑组合** + **嵌套分组**。

### 4.1 简写格式（自动推导操作符）

| 写法 | 推导为 | SQL 等价 |
|------|--------|----------|
| `{"status": 1}` | eq | `WHERE status = 1` |
| `{"status": [1, 2]}` | in | `WHERE status IN (1, 2)` |

### 4.2 标准格式

```json
{"字段名": {"op": "操作符", "value": "值"}}
```

### 4.3 完整操作符列表（19 个）

#### 比较操作符

| 操作符 | 含义 | 示例 | SQL 等价 |
|--------|------|------|----------|
| `eq` | 等于 | `{"status": {"op": "eq", "value": 1}}` | `status = 1` |
| `neq` | 不等于 | `{"status": {"op": "neq", "value": 0}}` | `status != 0` |
| `gt` | 大于 | `{"id": {"op": "gt", "value": 100}}` | `id > 100` |
| `gte` | 大于等于 | `{"price": {"op": "gte", "value": 10}}` | `price >= 10` |
| `lt` | 小于 | `{"id": {"op": "lt", "value": 50}}` | `id < 50` |
| `lte` | 小于等于 | `{"price": {"op": "lte", "value": 100}}` | `price <= 100` |

#### 集合操作符

| 操作符 | 含义 | 示例 | SQL 等价 |
|--------|------|------|----------|
| `in` | 在列表中 | `{"status": {"op": "in", "value": [1, 2]}}` | `status IN (1, 2)` |
| `not_in` | 不在列表中 | `{"id": {"op": "not_in", "value": [4, 5]}}` | `id NOT IN (4, 5)` |

#### 范围操作符

| 操作符 | 含义 | 示例 | SQL 等价 |
|--------|------|------|----------|
| `between` | 在范围内 | `{"price": {"op": "between", "value": [10, 100]}}` | `price BETWEEN 10 AND 100` |
| `not_between` | 不在范围内 | `{"price": {"op": "not_between", "value": [10, 100]}}` | `price NOT BETWEEN 10 AND 100` |
| `date_between` | 日期范围 | `{"created_at": {"op": "date_between", "value": ["2026-01-01", "2026-12-31"]}}` | `created_at BETWEEN ...` |

#### 模糊搜索操作符

| 操作符 | 含义 | 示例 | SQL 等价 |
|--------|------|------|----------|
| `like` | 包含 | `{"name": {"op": "like", "value": "张"}}` | `name ILIKE '%张%'` |
| `not_like` | 不包含 | `{"name": {"op": "not_like", "value": "test"}}` | `name NOT ILIKE '%test%'` |
| `prefix` | 以...开头 | `{"name": {"op": "prefix", "value": "张"}}` | `name ILIKE '张%'` |
| `suffix` | 以...结尾 | `{"email": {"op": "suffix", "value": "@qq.com"}}` | `email ILIKE '%@qq.com'` |

> 注：模糊搜索全部为大小写不敏感（ILIKE）

#### 空值操作符

| 操作符 | 含义 | 示例 | SQL 等价 |
|--------|------|------|----------|
| `is_null` | 为 NULL | `{"deleted_at": {"op": "is_null"}}` | `deleted_at IS NULL` |
| `not_null` | 不为 NULL | `{"email": {"op": "not_null"}}` | `email IS NOT NULL` |
| `is_empty` | 为 NULL 或空字符串 | `{"remark": {"op": "is_empty"}}` | `remark IS NULL OR remark = ''` |
| `not_empty` | 不为 NULL 且非空 | `{"remark": {"op": "not_empty"}}` | `remark IS NOT NULL AND remark != ''` |

> 注：`is_null` 只判断 NULL，`is_empty` 同时判断 NULL 和空字符串 `""`

### 4.4 逻辑组合

#### 默认 AND 关系

多个字段条件之间默认是 **AND** 关系：

```json
{
  "status": 1,
  "username": {"op": "like", "value": "zhang"}
}
```
→ `WHERE status = 1 AND username ILIKE '%zhang%'`

#### $or — 或关系

```json
{
  "$or": [
    {"status": 1},
    {"status": 2}
  ]
}
```
→ `WHERE (status = 1 OR status = 2)`

#### $and — 显式与关系

```json
{
  "$and": [
    {"status": 1},
    {"username": {"op": "like", "value": "admin"}}
  ]
}
```
→ `WHERE (status = 1 AND username ILIKE '%admin%')`

#### 任意嵌套

`$and` 和 `$or` 可以任意嵌套，表达复杂条件：

```json
{
  "$and": [
    {"status": 1},
    {"$or": [
      {"username": {"op": "like", "value": "admin"}},
      {"email": {"op": "suffix", "value": "@vip.com"}}
    ]}
  ]
}
```
→ `WHERE status = 1 AND (username ILIKE '%admin%' OR email ILIKE '%@vip.com')`

### 4.5 keyword 关键词搜索

独立于 `filters` 之外的参数，一个关键词同时搜索多个字段（OR 关系）：

```
GET /api/admin/user/getList?keyword=张三
```

搜索的目标字段由后端 Logic 的 `keyword_fields()` 方法定义：

```python
def keyword_fields(self):
    return ["username", "nickname", "email", "phone"]
```

→ `WHERE (username ILIKE '%张三%' OR nickname ILIKE '%张三%' OR email ILIKE '%张三%' OR phone ILIKE '%张三%')`

keyword 和 filters 可以同时使用，两者是 **AND** 关系。

### 4.6 空值处理规则

| 传入值 | 行为 |
|--------|------|
| 不传该字段 | 跳过，不作为条件 |
| `null` | 跳过（简写模式下） |
| `""` (空字符串) | 跳过（简写模式下） |
| `0` | **保留**，作为 eq 条件 |
| `false` | **保留**，作为 eq 条件 |

> 如需显式查询 NULL 或空字符串，使用 `is_null` / `is_empty` 操作符

### 4.7 安全机制

- **字段白名单**：后端通过 `allowed_filters()` 定义允许过滤的字段，不在白名单内的字段被静默忽略
- **排序白名单**：后端通过 `allowed_sorts()` 定义允许排序的字段
- **操作符白名单**：只有 19 个已知操作符被接受，非法操作符被忽略
- **SQL 注入防护**：所有条件通过 SQLAlchemy ORM 构建，不存在拼接 SQL 的风险

---

## 五、前端对接示例

### 5.1 获取列表（基础）

```ts
const res = await axios.get('/api/admin/user/getList', {
  params: {
    page: 1,
    pageSize: 20,
    sortField: 'id',
    sortOrder: 'desc',
    filters: JSON.stringify({ status: 1 }),
  },
});
const { list, total, page, pageSize } = res.data.data;
```

### 5.2 关键词 + 筛选组合

```ts
const res = await axios.get('/api/admin/user/getList', {
  params: {
    keyword: '张三',
    filters: JSON.stringify({ status: 1 }),
  },
});
```

### 5.3 日期范围查询

```ts
const res = await axios.get('/api/admin/order/getList', {
  params: {
    filters: JSON.stringify({
      created_at: {
        op: 'date_between',
        value: ['2026-03-01T00:00:00', '2026-03-31T23:59:59'],
      },
    }),
  },
});
```

### 5.4 OR 条件

```ts
const res = await axios.get('/api/admin/user/getList', {
  params: {
    filters: JSON.stringify({
      $or: [
        { status: 1 },
        { is_super_admin: true },
      ],
    }),
  },
});
```

### 5.5 复杂嵌套

```ts
const res = await axios.get('/api/admin/user/getList', {
  params: {
    filters: JSON.stringify({
      $and: [
        { status: 1 },
        { $or: [
          { username: { op: 'prefix', value: 'admin' } },
          { email: { op: 'suffix', value: '@company.com' } },
        ]},
      ],
    }),
  },
});
```

### 5.6 创建记录

```ts
await axios.post('/api/admin/user/doEdit', {
  username: 'zhangsan',
  password: '123456',
  nickname: '张三',
});
```

### 5.7 编辑记录

```ts
await axios.post('/api/admin/user/doEdit', {
  id: 2,
  nickname: '张三丰',
});
```

### 5.8 删除记录

```ts
// 单个
await axios.post('/api/admin/user/doDelete', { ids: 5 });
// 批量
await axios.post('/api/admin/user/doDelete', { ids: [1, 2, 3] });
// 逗号分隔
await axios.post('/api/admin/user/doDelete', { ids: '1,2,3' });
```

---

## 六、接口路径规则

```
/api/{端}/{模块}/{方法}
```

| 组成 | 说明 | 示例 |
|------|------|------|
| 端 | 路由分组前缀 | admin / client |
| 模块 | Route Manifest 中 `crud()` 的 prefix | user / setting |
| 方法 | 接口名 | getList / getDetail / doEdit / doDelete |

**示例路径：**

```
/api/admin/user/getList         # 管理员 - 用户列表
/api/admin/user/login           # 管理员 - 登录（公开）
/api/admin/user/info            # 管理员 - 当前用户信息
/api/admin/user/changePassword  # 管理员 - 修改密码
/api/admin/user/logout          # 管理员 - 退出登录
/api/admin/setting/get          # 管理员 - 读取配置（公开）
/api/admin/setting/set          # 管理员 - 写入配置
```

---

## 七、BaseLogic 子类配置参考

每个业务 Logic 继承 `BaseLogic`，通过类属性和方法覆写来定制行为：

```python
class AdminUserLogic(BaseLogic):
    # ── 类属性 ──
    model = AdminUser                    # 对应的 Model
    cache_prefix = "admin_user"          # Redis 缓存前缀（空字符串 = 不缓存）
    cache_fields = ["username"]          # 额外缓存字段（除主键外）
    except_keys = ["password"]           # 输出时过滤的敏感字段
    cache_ttl = 3600                     # 缓存过期时间（秒）

    # ── 白名单 ──
    def allowed_filters(self):           # 允许 filters 中出现的字段
        return ["id", "username", "status", "created_at"]

    def allowed_sorts(self):             # 允许排序的字段
        return ["id", "created_at", "updated_at", "status"]

    def keyword_fields(self):            # keyword 搜索的目标字段
        return ["username", "nickname", "email", "phone"]

    # ── 生命周期钩子 ──
    def format_save_data(self, data, is_update=False):  # 入库前格式化
    def before_create(self, data):       # 创建前（如密码加密）
    def before_edit(self, data):         # 编辑前
    def before_delete(self, pk_value):   # 删除前（如关联校验）
    def after_delete(self, pk_value):    # 删除后（如清理关联数据）
    def format_data(self, record):       # 单条输出格式化

    # ── 业务断言 ──
    self.assert_true(condition, "错误信息", code=400)  # 条件不满足时抛 BizError

    # ── 事务 ──
    await self.transaction(db, callback)  # 事务包装
```

---

## 八、注意事项

1. **filters 必须是 JSON 字符串**：前端传参时用 `JSON.stringify()` 编码
2. **时间字段用 ISO 8601 格式**：如 `2026-03-31T00:00:00`
3. **分页从 1 开始**：`page=1` 是第一页
4. **doEdit 是创建和编辑的统一入口**：有 id 是编辑，没 id 是创建
5. **doDelete 的 ids 支持三种格式**：字符串、数组、单个数字
6. **空值不作为简写条件**：前端清空筛选项时传空字符串或不传，要显式查 NULL 用 `is_null`
7. **敏感字段已自动过滤**：password 等字段不会出现在 getList / getDetail 响应中
8. **白名单是安全边界**：`allowed_filters()` / `allowed_sorts()` 未声明的字段前端传了也不会生效
9. **操作日志自动记录**：admin 端所有 POST 请求自动记录操作日志（含耗时），密码参数脱敏为 `***`
10. **软删除自动识别**：Model 有 `deleted_at` 字段时 doDelete 走软删除，否则物理删除
