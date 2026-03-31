# Base 项目架构设计文档

## 设计哲学

正如 Laravel 在 PHP 中还原了 .NET 的架构思想 —— 清晰分层、约定优于配置、目录即职责，
本项目在 Node.js（NestJS）中还原 PHP 的设计哲学：

- **Controller / Logic / Model 三层分离** — Controller 编排流程，Logic 封装业务，Model 定义数据
- **复用优先** — Base 类提供通用能力，子类继承即用，override 即扩展
- **CurdController 零代码 CRUD** — 子类显式声明 Logic，即拥有 getList/getDetail/doEdit/doDelete
- **解耦组合** — 新功能 = 新 Logic，Controller 编排调用，绝不侵入已有 Logic
- **防御式 early-return** — 每个方法自己校验前置条件，不合格立即返回
- **无感知设计** — 缓存、脱敏等横切关注点，调用方不判断开关，底层方法自行决定是否生效
- **目录名即职责** — 不设 common 垃圾桶，每个目录的名字就说明了它装什么

## PHP → NestJS 优雅化改进

### 1. except 参数级控制，命名语义化

**PHP 的问题**：`disableExcept()` 修改实例状态，后续调用受污染；且"except"是双重否定，语义不清。

**NestJS 方案**：参数控制 + 命名改为 `withSensitive`（"本次要带敏感字段"）：

```ts
getDetail(1)                              // 默认过滤敏感字段
getDetail(1, { withSensitive: true })     // 本次带上 password 等，不影响下次
```

### 2. CurdController 显式声明替代魔法推导

NestJS 的 DI 注入天然解决，无需 `guessLogicClass()` 字符串替换。

### 3. formatSaveData 职责收窄

时间戳交给 Prisma `@default(now())` / `@updatedAt`。
JSON 序列化只对非 Prisma Json 类型的 String 字段处理（Prisma Json 类型自动处理）。
子类扩展通过 beforeCreate/beforeEdit 钩子，不混在 formatSaveData 里。

### 4. 依赖注入替代 getInstance() + new 混用

NestJS DI 容器天然单例，无需手动管理。

### 5. Redis 操作统一走 RedisService

不再出现 `new Redis()` 直连代码。

### 6. Token 改用 JWT + token_version 缓存

PHP 的自生成 token + Redis 存储方案改为标准 JWT。
token_version 缓存到 Redis，避免每次请求查 DB：

```
JWT 验证（无状态）→ 提取 userId + tokenVersion
→ Redis 查 ${APP_NAME}:TOKEN_VER:${userId}
→ 未命中 → DB 查询 → 写入 Redis 缓存
→ 比对 tokenVersion → 不匹配则 token 失效
```

## 技术栈

- NestJS + TypeScript
- PostgreSQL + Prisma 7
- Redis（缓存层）
- JWT 认证（支持可选单点登录）

---

## 一、目录结构

```
src/
├── controllers/                     # 控制器 — 流程编排，按端分组
│   ├── BaseController.ts            #   基类：response() / success() / fail() / handleException()
│   ├── CurdController.ts            #   CRUD 基类：零代码 getList/getDetail/doEdit/doDelete
│   ├── admin/                       #   管理员后台
│   │   ├── UserController.ts
│   │   ├── MenuController.ts
│   │   ├── RoleController.ts
│   │   ├── PermissionController.ts
│   │   └── OrderController.ts
│   └── client/                      #   用户后台
│       ├── UserController.ts
│       └── OrderController.ts
│
├── logics/                          # 逻辑层 — 业务处理，跨端共享
│   ├── BaseLogic.ts                 #   基类：CRUD + 缓存 + 格式化
│   ├── UserLogic.ts
│   ├── MenuLogic.ts
│   ├── RoleLogic.ts
│   ├── PermissionLogic.ts
│   ├── OrderLogic.ts
│   ├── SettingLogic.ts              #   系统配置（继承 BaseLogic + 全量缓存扩展）
│   └── CommissionLogic.ts
│
├── models/                          # 模型层 — 类型定义、DTO
│   ├── User.ts
│   └── Order.ts
│
├── routes/                          # 路由 — 每个文件 = 一个端，单文件管理
│   ├── admin.ts                     #   /api/admin/*
│   └── client.ts                    #   /api/client/*
│
├── guards/                          # 守卫 — 认证与权限
│   ├── AuthGuard.ts                 #   JWT 认证（通用）
│   ├── PermissionGuard.ts           #   RBAC 权限（admin 端）
│   └── ClientGuard.ts               #   用户端守卫
│
├── decorators/                      # 自定义装饰器
│   ├── Public.ts                    #   @Public() 标记无需认证
│   └── Actions.ts                   #   @Actions() 声明操作映射
│
├── interceptors/                    # 拦截器 — 响应格式化
│   └── ResponseInterceptor.ts
│
├── filters/                         # 过滤器 — 异常处理
│   └── GlobalExceptionFilter.ts
│
├── services/                        # 服务 — 基础设施
│   ├── PrismaService.ts             #   数据库连接
│   └── RedisService.ts              #   缓存连接，全局可注入
│
├── utils/                           # 工具 — 通用工具函数
│   └── Token.ts                     #   JWT 签发与验证
│
├── app.module.ts                    # 根模块
└── main.ts                          # 入口
```

### 为什么这样分

| 目录 | 对应 PHP 项目 | 职责 |
|------|-------------|------|
| controllers/ | app/controller/ | 接收请求，编排 Logic，返回响应 |
| controllers/CurdController | app/controller/CurdController.php | 零代码 CRUD |
| logics/ | app/logic/ | 业务逻辑，可被任何 Controller 调用 |
| models/ | app/model/ | 数据结构定义 |
| routes/ | config/route.php | 路由注册，一个文件一个端 |
| guards/ | app/middleware/ | 认证 + 权限拦截 |
| decorators/ | PHP 反射读属性 | 元数据声明（@Public、@Actions） |
| interceptors/ | 无直接对应 | 后置响应包装 |
| filters/ | app/handler/ExceptionHandler.php | 异常统一处理 |
| services/ | support/Cache, support/Db | 基础设施服务 |
| utils/ | app/util/ | 通用工具函数 |

---

## 二、路由设计

### 单进程，前缀分组

```
/api/admin/*      → 管理员后台（AuthGuard + PermissionGuard）
/api/client/*     → 用户后台（AuthGuard + ClientGuard）
/api/channel/*    → 渠道端（按需）
/api/open/*       → 开放接口（按需）
```

### 路由目录化

```
src/routes/
├── admin.ts       # 管理员后台路由
├── client.ts      # 用户后台路由
└── channel.ts     # 渠道端路由（按需新增）
```

---

## 三、BaseController

```ts
class BaseController {
  response(code, message, data)            // { code, msg, data }
  success(data?, message?, code?)          // code=0
  fail(message?, code?, data?)             // code=1
  handleException(error)                   // APP_DEBUG 控制详细程度
}
```

---

## 四、CurdController

```ts
class CurdController extends BaseController {
  protected logic: BaseLogic;
  protected bindUserColumn?: string;       // 数据行级隔离字段名（如 'operator_id'）

  getList()          // 自动读取请求参数 + bindUserId 过滤
  getDetail()        // 支持主键或条件对象
  doEdit()           // createOrUpdate
  doDelete()         // 支持批量 ids: "1,2,3"
}
```

### bindUserId 流程

```
请求 → AuthGuard 验证身份 → PermissionGuard 查权限
  → allowed_actions.bindUserId = true?
    → 是 → 将 { shouldBind: true, userId } 挂到请求上下文
    → 否 → 不挂
  → 超级管理员 → 跳过绑定

CurdController.getList()
  → 读取请求上下文 shouldBind
  → 如果 shouldBind && this.bindUserColumn 有值
    → 自动追加 where { [bindUserColumn]: userId }
  → 调用 logic.getList(...)
```

Guard 决定"要不要绑定"，Controller 声明"绑哪个字段"。职责清晰分离。

---

## 五、BaseLogic 完整设计

### 5.1 基础属性

```ts
class BaseLogic<T = any> {
  protected modelDelegate: any;              // Prisma 表操作对象
  protected primaryKeyName: string = 'id';   // 主键字段名

  protected needCache: boolean = false;      // 是否开启缓存
  protected cachePrefix: string = '';        // 自动推导：UserLogic → 'User'
  protected cacheExpire: number = 86400;     // 缓存 TTL（秒）
  protected cacheKeys: string[] = [];        // 多字段缓存索引

  protected exceptKeys: string[] = [];       // 输出时排除的敏感字段
}
```

### 5.2 CRUD 方法

```
getList(queryData)
  → 提取 page/pageSize/sortField/sortOrder/filters
  → 校验 sortField 合法性（防注入）
  → buildWhere(filters) → DB 查询 → formatList → return { list, total }

getDetail(primaryValueOrConditions, options?)
  → 标量值 → 走缓存 → 未命中走 DB → 写缓存
  → 对象/数组 → 检查 key 是否在 cacheKeys 中 → 在则走缓存
  → formatOutput(record, options) → return

createOrUpdate(data)
  → 有主键 → doEdit → 返回最新数据
  → 无主键 → doCreate

doCreate(data)
  → formatSaveData → beforeCreate 钩子 → DB 插入 → createCache → formatOutput

doEdit(primaryValue, data)                    ← 修复：多字段缓存安全更新
  → DB 查询旧记录（拿到旧的 cacheKey 字段值）
  → formatSaveData → beforeEdit 钩子
  → DB 事务更新
  → DB 查询新记录
  → clearCache(旧记录)                        ← 用旧值清（card_id 可能变了）
  → createCache(新记录)                       ← 用新值建
  → formatOutput(新记录)

doDelete(primaryValueOrList)
  → 支持批量，DB 事务包裹
  → beforeDelete → DB 查询记录 → DB 删除 → clearCache(记录) → afterDelete
```

### 5.3 生命周期钩子

```ts
protected beforeCreate(data): data
protected beforeEdit(data): data
protected beforeDelete(id): void     // 抛异常可阻止删除
protected afterDelete(id): void      // 清理关联数据
```

### 5.4 缓存方法

Key 格式：`${APP_NAME}:${cachePrefix}:${fieldName}:${fieldValue}`

```
getFromCache(field, value)
  → if (!needCache) return null
  → redis.get(key) → JSON.parse → return

createCache(record)
  → if (!needCache) return
  → keys = cacheKeys.length ? cacheKeys : [primaryKeyName]
  → for key of keys:
      redis.set(key, JSON.stringify(record), cacheExpire)

clearCache(record)
  → if (!needCache) return
  → keys = cacheKeys.length ? cacheKeys : [primaryKeyName]
  → for key of keys:
      redis.del(key)

rebuildCacheAll()
  → if (!needCache) return
  → 使用 SCAN（非 KEYS）模糊匹配删除 ${cachePrefix}:* 全部缓存
  → 避免 Redis KEYS 命令在大数据量下阻塞
```

### 5.5 数据格式化

```
formatSaveData(data)
  → 仅对非 Prisma Json 类型的 String 字段做 JSON.stringify
  → Prisma Json 类型字段自动处理，无需手动转换
  → 子类可 override 扩展

formatOutput(record, options?)
  → JSON 字符串字段 → 尝试 JSON.parse 还原
  → 如果 options.withSensitive !== true 且 exceptKeys 有值
      → 删除 exceptKeys 中的字段
  → 子类可 override（补充计算字段等）

formatList(records)
  → 逐条 formatOutput
```

### 5.6 条件构建（扩展版）

```
buildWhere(conditions)
  → 空值跳过（保留 0 和 false）
  → 数组 → { in: [...] }
  → 普通值 → equals
  → 对象 → 透传 Prisma 操作符（支持高级查询）
```

支持的操作符示例：

```ts
filters: {
  status: 1,                                     // equals
  role_id: [1, 2, 3],                             // in
  created_at: { gte: '2026-01-01', lte: '2026-12-31' },  // 范围
  username: { contains: 'admin' },                // 模糊
  price: { gt: 100 },                             // 大于
}
```

Prisma 原生支持这些操作符，buildWhere 识别对象类型时直接透传，零额外开发成本。

---

## 六、SettingLogic — 系统配置

**继承 BaseLogic**（管理后台 CRUD）+ **扩展 get/set 静态方法**（程序调用）。

### 6.1 数据模型

```
settings 表
├── id          Int       @id
├── category    String    配置类别
├── name        String    配置键
├── label       String?   显示名称
├── value       String    配置值（JSON 序列化）
├── remark      String?   备注说明
├── created_at  DateTime
├── updated_at  DateTime
└── @@unique([category, name])
```

### 6.2 双重身份

```ts
class SettingLogic extends BaseLogic {
  // ===== 继承 BaseLogic 的 CRUD（管理后台用）=====
  // getList / getDetail / createOrUpdate / doDelete 自动可用
  // 管理后台的 SettingController 直接继承 CurdController 即可

  // ===== 扩展的全量缓存方法（程序调用用）=====
  static async get(category, key?)     // 读配置
  static async set(category, key, val) // 写配置（upsert + 刷缓存）
  static async makeCache()             // 重建全量缓存
}
```

### 6.3 全量缓存

```
Key：${APP_NAME}:SETTINGS
结构：{ category: { key: value } }
TTL：365 天，写入时主动刷新
```

管理后台的 CRUD 操作（通过 BaseLogic）和程序的 set() 操作，都在写入后调用 makeCache() 刷新。

### 6.4 通过 CRUD 管理 vs 通过 get/set 调用

```ts
// 管理后台 — 走 CurdController 标准 CRUD
POST /api/admin/setting/doEdit { category: 'sms', name: 'provider', value: 'aliyun', label: '短信服务商' }

// 程序内部 — 走 SettingLogic 静态方法
const provider = await SettingLogic.get('sms', 'provider');
await SettingLogic.set('sms', 'provider', 'tencent');
```

---

## 七、RBAC 权限体系

### 7.1 数据模型

```
users ──1:N──→ user_roles ──N:M──→ role_permissions ──N:1──→ roles
                                          │
                                          N:M
                                          ▼
                               permission_menus ──N:1──→ menus
                               │ permission_id
                               │ menu_id
                               │ allowed_actions (JSON)
```

#### menus 表（菜单树）

```
├── id          Int
├── parent_id   Int       0=顶级
├── name        String    菜单标识
├── title       String    显示名称
├── icon        String?
├── path        String?   前端路由
├── component   String?   前端组件
├── order       Int       排序
├── hidden      Boolean   是否隐藏
├── meta        Json?     扩展元数据
├── created_at  DateTime
└── updated_at  DateTime
```

#### permission_menus.allowed_actions

```json
{ "read": true, "write": true, "delete": false, "bindUserId": true }
```

| 操作 | 含义 |
|------|------|
| read | 可查看（getList / getDetail） |
| write | 可编辑（doEdit） |
| delete | 可删除（doDelete） |
| bindUserId | 只能操作自己创建的数据 |

### 7.2 权限校验流程（PermissionGuard）

```
请求 → AuthGuard 验证 JWT → 获取 userId
  → @Public() 路由 → 直接放行
  → 超级管理员 → 直接放行
  → 映射当前 action 到操作类型（read/write/delete）
  → 查询用户角色 → 权限 → 菜单 + allowed_actions
  → 当前 Controller 对应的菜单是否在权限中？
  → 对应操作类型的 allowed_actions 是否 true？
  → bindUserId=true → 注入过滤条件到请求上下文
  → 通过 / 拒绝
```

### 7.3 操作类型映射

```ts
// 默认映射
{ read: ['getList', 'getDetail'], write: ['doEdit'], delete: ['doDelete'] }

// Controller 用 @Actions 装饰器扩展
@Actions({ read: ['getList', 'getDetail', 'orderSummary', 'doExport'] })
class AdminOrderController extends CurdController { ... }
```

### 7.4 MenuLogic

```ts
class MenuLogic extends BaseLogic {
  needCache = true;
  cachePrefix = 'MENU';

  getMenus(userId?, toTree = false)
    → 超级管理员 → 返回全部菜单
    → 普通用户：
      → user_roles → role_permissions → permission_menus → 收集 menu_ids
      → 收集所有 parent_id（递归上溯到顶级）
      → 单次 IN 查询获取所有菜单（避免 N+1）
      → 缓存结果
    → toTree → 平铺转树形

  // 菜单/权限变更时
  clearAllMenuCache()
    → SCAN 匹配 MENU:* → 批量删除（不用 KEYS 命令）
}
```

缓存维度：

```
${APP_NAME}:MENU:ALL              → 全部菜单
${APP_NAME}:MENU:USER:{userId}    → 用户级
${APP_NAME}:MENU:ROLE:{roleId}    → 角色级
```

### 7.5 RoleLogic

```ts
class RoleLogic extends BaseLogic {
  createOrUpdate(data)
    → 提取 permissions 数组
    → 父类 createOrUpdate 保存角色
    → 删除旧 role_permissions → 批量插入新的
    → 清除相关菜单缓存
}
```

---

## 八、Guard 体系

### 8.1 层级

```
全局注册
├── AuthGuard                    # JWT 认证
│   → @Public() 路由跳过
│   → 验证 JWT → 校验 token_version（先查 Redis 缓存，未命中查 DB）
│   → 将 user 挂到请求上下文

admin 路由组注册
├── PermissionGuard              # RBAC 操作级权限
│   → 超级管理员放行
│   → 操作类型映射 → 查权限 → 校验 allowed_actions
│   → bindUserId 处理

client 路由组注册
├── ClientGuard                  # 用户端（已登录即可）
```

### 8.2 装饰器

```ts
@Public()                        // 无需认证（登录接口、获取基础配置等）
@Actions({                       // 扩展操作映射
  read: ['getList', 'getDetail', 'orderSummary'],
})
```

### 8.3 PHP 中间件对照

| PHP | NestJS | 说明 |
|-----|--------|------|
| CorsMiddleware | `app.enableCors()` | 一行搞定 |
| AdminMiddleware | AuthGuard + PermissionGuard | 拆分认证和权限 |
| ClientMiddleware | AuthGuard + ClientGuard | 业务级校验 |
| ChannelMiddleware | ChannelGuard（按需） | IP白名单+签名 |
| ValidatorMiddleware | ValidationPipe + class-validator DTO | 内置，类型安全 |

### 8.4 参数校验

```ts
// DTO 定义
class CreateOrderDto {
  @IsString() @IsNotEmpty()
  productName: string;

  @IsNumber() @Min(0)
  price: number;
}

// Controller 使用
@Post('create')
async create(@Body() body: CreateOrderDto) { ... }
```

全局 ValidationPipe 在 main.ts 注册，自动生效。

---

## 九、RedisService

```ts
class RedisService {
  get(key): Promise<string | null>
  set(key, value, expireSeconds?): Promise<void>
  del(key | keys[]): Promise<void>
  exists(key): Promise<boolean>
  scan(pattern, count?): Promise<string[]>       // 替代 KEYS，生产安全
  expire(key, seconds): Promise<void>
  ttl(key): Promise<number>
  incr(key): Promise<number>
  decr(key): Promise<number>
}
```

注意：使用 `scan` 替代 `keys`，避免大数据量下阻塞 Redis。

---

## 十、认证

- JWT 签发与验证（utils/Token.ts）
- User 表 `token_version` 字段
- token_version 缓存到 Redis（Key: `${APP_NAME}:TOKEN_VER:${userId}`）
  - 登录时写入/更新 Redis
  - 验证时先查 Redis，未命中再查 DB 并缓存
  - SSO 开启时 token_version++ → 旧 token 自动失效
- `@Public()` 装饰器标记无需认证的路由
- .env 配置 `SSO_ENABLED=true/false`

---

## 十一、环境配置 .env

```env
# 应用
APP_NAME=base
APP_DEBUG=false

# 数据库
DATABASE_HOST=localhost
DATABASE_PORT=5432
DATABASE_NAME=base
DATABASE_USER=postgres
DATABASE_PASSWORD=postgres
DATABASE_SCHEMA=public

# Redis
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_PASSWORD=
REDIS_DB=0

# 服务
PORT=3000

# 认证
JWT_SECRET=your-secret-key
JWT_EXPIRES_IN=7d
SSO_ENABLED=false
```

---

## 附录：自审改进清单

以下是对原 PHP 设计和初版文档的深度自审，已全部反映在上述文档中：

| # | 发现的问题 | 改进方案 |
|---|-----------|---------|
| 1 | doEdit 多字段缓存只删主键，cacheKey 字段值变化后旧缓存成脏数据 | doEdit 必须先查旧记录 → clearCache(旧) → 更新 → createCache(新) |
| 2 | `withExcept: true` 是双重否定，语义不清 | 改为 `withSensitive: true`（"本次要带敏感字段"）|
| 3 | token_version 每次请求查 DB，JWT 无状态优势消失 | token_version 缓存到 Redis，大部分请求只查 Redis |
| 4 | buildWhere 只支持 equals 和 in，真实场景不够 | 对象类型透传 Prisma 操作符（gte/lte/contains/gt 等） |
| 5 | SettingLogic 独立于 BaseLogic，管理后台 CRUD 能力浪费 | 继承 BaseLogic + 扩展 get/set 静态方法，双重身份 |
| 6 | formatSaveData 对所有字段无脑 JSON.stringify | 仅对非 Prisma Json 类型的 String 字段处理 |
| 7 | 菜单树补全父级 N+1 查询 | 一次收集所有 ID（含父级递归），单次 IN 查询 |
| 8 | Redis KEYS 命令在大数据量下阻塞 | 改用 SCAN，RedisService 提供 scan 方法 |
| 9 | PHP 反射读 Controller 属性（needAuth/actions）不够显式 | 用 @Public() / @Actions() 装饰器 |
| 10 | Guard 和 Controller 的 bindUserId 如何衔接 | Guard 挂请求上下文，Controller 声明 bindUserColumn，职责分离 |
