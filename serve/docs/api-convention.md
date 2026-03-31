# 前后端 CRUD 接口约定文档

本文档定义了前端与后端交互的 **CRUD 接口规范**，包括请求格式、响应格式、filters 查询语法和约束条件。
所有继承 `CurdController` 的接口均遵守此约定。

---

## 一、统一响应格式

所有接口返回 **HTTP 200**，通过 `code` 字段区分业务状态：

```json
{
  "code": 0,        // 0=成功，非0=失败（1=通用失败，401=未登录，403=无权限）
  "msg": "success", // 提示信息
  "data": {}        // 业务数据，失败时为 null
}
```

---

## 二、认证

除标记 `@Public()` 的接口外，所有请求需在 Header 中携带 JWT Token：

```
Authorization: Bearer <token>
```

Token 过期或版本号不匹配时返回：

```json
{ "code": 401, "msg": "请登录", "data": null }
```

---

## 三、CRUD 四个标准接口

每个继承 `CurdController` 的模块自动拥有以下 4 个接口：

### 3.1 获取列表 — `GET /{module}/getList`

**请求参数（Query）：**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| page | number | 否 | 1 | 当前页码 |
| pageSize | number | 否 | 20 | 每页条数 |
| sortField | string | 否 | id | 排序字段 |
| sortOrder | string | 否 | desc | 排序方向：asc / desc |
| filters | string(JSON) | 否 | {} | 筛选条件（JSON 字符串，详见第四章） |

**请求示例：**

```
GET /api/admin/user/getList?page=1&pageSize=10&sortField=id&sortOrder=desc&filters={"status":1}
```

**响应：**

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "list": [
      { "id": 1, "username": "admin", "nickname": "超级管理员", "status": 1, ... }
    ],
    "total": 50
  }
}
```

**约束：**
- `pageSize` 前端建议限制范围 1-100，后端暂不强制校验
- `sortField` 后端会校验字段合法性，非法字段回退为主键
- `filters` 必须是合法 JSON 字符串，解析失败时视为空条件
- 响应中的 `list` 已经过 `formatOutput` 处理，敏感字段（如 password）已过滤

---

### 3.2 获取详情 — `GET /{module}/getDetail`

**请求参数（Query）：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| id | number | 是 | 主键值（字段名随模型变化，默认 id） |

**请求示例：**

```
GET /api/admin/user/getDetail?id=1
```

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
- 记录不存在时返回 `{ "code": 1, "msg": "数据不存在" }`
- 如果模块开启了 `bindUserId`，只能查看自己创建的数据

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

**响应：**

```json
{
  "code": 0,
  "msg": "success",
  "data": { "id": 2, "username": "zhangsan", "nickname": "张三丰", ... }
}
```

**约束：**
- 创建和编辑共用一个接口，**有主键 = 编辑，无主键 = 创建**
- 编辑时，未传的字段不会被修改（只更新传入的字段）
- 密码字段在 Logic 的 `beforeCreate` / `beforeEdit` 钩子中自动加密
- 编辑时不传 password 则不修改密码，传了则更新为新密码

---

### 3.4 删除 — `POST /{module}/doDelete`

**请求体（JSON）：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| ids | string / number / number[] | 是 | 要删除的主键，支持多种格式 |

**支持的 ids 格式：**

```json
// 格式一：逗号分隔字符串
{ "ids": "1,2,3" }

// 格式二：数组
{ "ids": [1, 2, 3] }

// 格式三：单个值
{ "ids": 5 }
```

**响应：**

```json
{ "code": 0, "msg": "删除成功", "data": null }
```

**约束：**
- `ids` 不能为空，否则返回 `{ "code": 1, "msg": "缺少 ids 参数" }`
- 逐条删除，每条都会触发 `beforeDelete` / `afterDelete` 钩子
- 如果某条记录 `beforeDelete` 钩子抛出异常，该条不会被删除，但不影响其他条

---

## 四、Filters 查询语法

`filters` 是 `getList` 接口的核心查询参数，支持多种查询方式。

### 4.1 精确匹配

```json
{ "status": 1 }
```

生成 SQL：`WHERE status = 1`

### 4.2 IN 查询（多值匹配）

```json
{ "status": [1, 2] }
```

生成 SQL：`WHERE status IN (1, 2)`

### 4.3 范围查询

```json
{
  "created_at": {
    "gte": "2026-01-01T00:00:00Z",
    "lte": "2026-12-31T23:59:59Z"
  }
}
```

生成 SQL：`WHERE created_at >= '2026-01-01' AND created_at <= '2026-12-31'`

### 4.4 模糊搜索

```json
{ "username": { "contains": "admin" } }
```

生成 SQL：`WHERE username LIKE '%admin%'`

### 4.5 大于 / 小于

```json
{ "id": { "gt": 100 } }
```

```json
{ "price": { "lt": 50.00 } }
```

### 4.6 不等于

```json
{ "status": { "not": 0 } }
```

### 4.7 组合查询

多个字段条件之间是 **AND** 关系：

```json
{
  "status": 1,
  "username": { "contains": "zhang" },
  "created_at": { "gte": "2026-01-01T00:00:00Z" }
}
```

生成 SQL：`WHERE status = 1 AND username LIKE '%zhang%' AND created_at >= '2026-01-01'`

### 4.8 空值处理规则

| 值 | 行为 |
|----|------|
| `undefined` | 跳过，不作为条件 |
| `null` | 跳过，不作为条件 |
| `""` (空字符串) | 跳过，不作为条件 |
| `0` | **保留**，作为精确匹配条件 |
| `false` | **保留**，作为精确匹配条件 |

**前端传参注意**：如果某个筛选条件用户未填写，应传空字符串或不传该字段，而不是传 `0`。

### 4.9 完整的操作符列表

| 操作符 | 含义 | 示例 |
|--------|------|------|
| （直接传值） | 精确匹配 | `{ "status": 1 }` |
| （传数组） | IN 匹配 | `{ "status": [1, 2] }` |
| `gt` | 大于 | `{ "id": { "gt": 100 } }` |
| `gte` | 大于等于 | `{ "price": { "gte": 10 } }` |
| `lt` | 小于 | `{ "id": { "lt": 50 } }` |
| `lte` | 小于等于 | `{ "price": { "lte": 100 } }` |
| `contains` | 包含（模糊搜索） | `{ "name": { "contains": "张" } }` |
| `startsWith` | 以...开头 | `{ "name": { "startsWith": "张" } }` |
| `endsWith` | 以...结尾 | `{ "email": { "endsWith": "@qq.com" } }` |
| `not` | 不等于 | `{ "status": { "not": 0 } }` |
| `in` | 显式 IN | `{ "id": { "in": [1, 2, 3] } }` |
| `notIn` | 不在列表中 | `{ "id": { "notIn": [4, 5] } }` |

---

## 五、前端对接示例（Axios）

### 5.1 获取列表

```ts
const res = await axios.get('/api/admin/user/getList', {
  params: {
    page: 1,
    pageSize: 20,
    sortField: 'id',
    sortOrder: 'desc',
    filters: JSON.stringify({
      status: 1,
      username: { contains: keyword },
    }),
  },
});

const { list, total } = res.data.data;
```

### 5.2 创建记录

```ts
const res = await axios.post('/api/admin/user/doEdit', {
  username: 'zhangsan',
  password: '123456',
  nickname: '张三',
});
```

### 5.3 编辑记录

```ts
const res = await axios.post('/api/admin/user/doEdit', {
  id: 2,
  nickname: '张三丰',
});
```

### 5.4 删除记录

```ts
// 单个删除
await axios.post('/api/admin/user/doDelete', { ids: 5 });

// 批量删除
await axios.post('/api/admin/user/doDelete', { ids: '1,2,3' });
```

### 5.5 日期范围查询

```ts
const res = await axios.get('/api/admin/order/getList', {
  params: {
    filters: JSON.stringify({
      created_at: {
        gte: '2026-03-01T00:00:00Z',
        lte: '2026-03-31T23:59:59Z',
      },
    }),
  },
});
```

---

## 六、接口路径规则

```
/api/{端}/{模块}/{方法}
```

| 组成 | 说明 | 示例 |
|------|------|------|
| 端 | 路由分组前缀 | admin / client |
| 模块 | Controller 的 @Controller('xxx') 值 | user / setting / order |
| 方法 | 接口名 | getList / getDetail / doEdit / doDelete |

**示例路径：**

```
/api/admin/user/getList       # 管理员 - 用户列表
/api/admin/user/login         # 管理员 - 登录（@Public）
/api/admin/setting/get        # 管理员 - 读取配置（@Public）
/api/admin/setting/set        # 管理员 - 写入配置
/api/client/user/getDetail    # 用户端 - 用户详情
```

---

## 七、错误码约定

| code | 含义 | 场景 |
|------|------|------|
| 0 | 成功 | 所有正常响应 |
| 1 | 通用失败 | 参数错误、业务逻辑失败 |
| 401 | 未认证 | 未登录、token 过期/失效 |
| 403 | 无权限 | RBAC 权限校验不通过 |
| 500 | 服务器错误 | 未捕获的异常 |

---

## 八、注意事项

1. **filters 必须是 JSON 字符串**：前端传参时用 `JSON.stringify()` 编码
2. **时间字段用 ISO 8601 格式**：如 `2026-03-31T00:00:00Z`
3. **分页从 1 开始**：`page=1` 是第一页，不是 `page=0`
4. **doEdit 是创建和编辑的统一入口**：有 id 是编辑，没 id 是创建
5. **doDelete 的 ids 支持三种格式**：字符串、数组、单个数字
6. **空值不作为条件**：前端清空筛选项时传空字符串或不传，不要传 null
7. **敏感字段已自动过滤**：password 等字段不会出现在 getList / getDetail 响应中
8. **操作日志自动记录**：admin 端所有请求自动记录操作日志，密码参数会脱敏为 `***`
