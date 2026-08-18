# Base Platform 集中式路由注册表重构方案

**状态**：已完成（v2.0.0）
**决策类型**：通用 Base 架构改进
**创建/最后更新**：2026-08-18
**当前基线**：FastAPI 0.141.1，159 个 OpenAPI operation / 159 个 path
**实施前提**：本文档是执行合同；实施不得顺带修改现有 URL、HTTP Method、鉴权、权限、响应或 OpenAPI 契约。

---

## 1. 问题定义

当前仓库没有真正的路由层：

- `serve/app/main.py` 只知道如何逐个 `include_router()`，不包含完整路由契约；
- URL、HTTP Method、Tag 和部分响应模型散落在 27 个 Controller 模块的 `@router.get/post(...)` 装饰器中；
- prefix 分别由 `main.py`、Controller 内的 `APIRouter(prefix=...)` 和 `crud_router(prefix)` 叠加；
- 鉴权和权限分别位于 Handler 签名中的 `Depends(...)`、Controller 模块局部的 `_perm_*` 工厂以及 `crud_router()` 内部；
- `serve/app/controllers/base.py::crud_router()` 通过闭包动态生成 5 类端点，静态搜索无法得到最终路由表；
- `serve/app/controllers/web/seo.py::indexnow_key_file()` 的 `/{name}` 是根路径兜底，当前靠 `main.py` 中的手工注册顺序避免遮蔽 `/health`；
- 系统缺少 Laravel `route:list` 对等能力，也没有对重复路径、缺失鉴权、悬空 Handler 和兜底顺序的构建阶段检查。

这个结构在 Controller 数量增长时线性恶化。任何全局路由改动都可能需要逐 Controller 检查，无法通过一个权威入口完成批量推理。

## 2. 目标与用户价值

### 2.1 目标

1. 建立唯一权威入口 `app.routes.register_routes(app)`。
2. 只允许 `serve/app/routes/` 定义 URL、Method、prefix、group middleware、permission、name、tag 和注册优先级。
3. Controller 只保留未装饰的 Handler，不再创建 `APIRouter`、不再使用 `@router.*`、不再 `include_router()`。
4. 提供类 Laravel 的 `group / middleware / get / post / put / patch / delete / options / match / any` DSL。
5. 标准 CRUD 以一条资源声明生成，不在 Controller 中动态注册。
6. 提供确定性全局路由目录与 `python -m app.routes list|check|json` CLI。
7. 在 CI 阶段而不是运行时发现重复路由、策略缺失、错误参数和危险兜底。
8. 首次重构对现有 API 完全兼容。

### 2.2 用户价值

- 查找和评估任意路由时只查 `routes/` 或生成目录，不遍历 Controller。
- Admin/Client 的 prefix、鉴权、审计策略通过 Group 一处修改。
- 通过 Route ID 和全局目录定位 Handler、权限、响应模型和源文件。
- 当路由规模达到数万时，维护对象仍是 Group 规则、资源和少量自定义 Action，而不是逐个 Handler 搜索。

## 3. 已确认决策

1. **集中的是权威和职责，不是把 25,000 条路由塞进一个巨型文件。**
2. 根入口仅为 `app.routes.register_routes(app)`；分片 Manifest 必须由 `build_registry()` 显式聚合，禁止隐式文件扫描和 Controller 自注册。
3. 引入 Laravel 风格 DSL，但保持 Python 可调用对象和 FastAPI 类型/依赖注入能力。
4. Group `middleware` 的本期语义是“Handler 之前执行、可短路返回的路由级策略”，由 FastAPI `Depends` 编译实现；包装 Response 的 ASGI middleware 仍属于 App 级，不在本次伪装成同一概念。
5. `get_db` 等 Handler 数据依赖仍留在 Handler 签名；鉴权和权限策略移至 Route Group/Route Spec。
6. 第一版只改路由定义机制，不将 `getList/doEdit` 改为 REST 命名，不改 camelCase/snake_case/kebab-case 现状。
7. 根兜底必须通过 `fallback()` 显式声明并由编译器放在最后，不再依赖 `main.py` 人工排序。
8. 所有 Route ID 必须唯一；没有明确 Route ID 的路由不得编译。

## 4. 目标架构

```text
Request
  → FastAPI app
  → app.routes.register_routes(app)           # 唯一注册入口
  → RouteRegistry.install()
  → Route Group middleware / permission
  → undecorated Controller Handler
  → Logic
  → Model/DB 或 Service+Driver
```

```text
serve/app/routes/
├── __init__.py        # build_registry() / register_routes(app)
├── __main__.py        # python -m app.routes list|check|json
├── types.py           # HttpMethod / RouteSpec / GroupSpec / RoutePriority
├── registry.py        # RouteRegistry / RouteGroup / RouteBuilder
├── resources.py       # CrudController + legacy_crud() 路由生成契约
├── catalog.py         # 标准化目录、diff、表格/JSON 输出
├── validation.py      # 碰撞、策略、参数、fallback 校验
├── system.py          # health + /uploads mount
├── admin.py           # /api/admin + /api/file
├── client.py          # /api/client
├── public.py          # /api/dict
├── web.py             # sitemap/robots/indexnow fallback
└── extensions.py      # 稳定的下游路由注册扩展点，Base 中为空

serve/app/controllers/
├── health.py          # health / health_live / health_ready Handler
├── crud.py            # CrudController HTTP Handler，不注册路由
├── admin/*.py         # 只有 Handler
├── client/*.py        # 只有 Handler
├── dict.py            # 只有 Handler
└── web/seo.py         # 只有 Handler
```

### 4.1 唯一入口

`serve/app/routes/__init__.py` 必须显式聚合，禁止运行时 glob 扫描：

```python
def build_registry() -> RouteRegistry:
    routes = RouteRegistry()
    register_system_routes(routes)
    register_public_routes(routes)
    register_admin_routes(routes)
    register_client_routes(routes)
    register_extension_routes(routes)
    register_web_routes(routes)
    return routes


def register_routes(app: FastAPI) -> None:
    routes = build_registry()
    routes.validate()
    routes.install(app)
```

`serve/app/main.py` 只保留：

```python
from app.routes import register_routes

register_routes(app)
```

`RouteRegistry.install()` 将普通 HTTP RouteSpec 与 fallback RouteSpec 分别编译为两个 `APIRouter`：先 `include_router(normal_router)`，再安装 MountSpec，最后 `include_router(fallback_router)`。这种拆分保证实际匹配顺序固定为普通 HTTP → Mount → Fallback；`APIRouter` 和 Mount 不用一个伪造的统一类型表示，但都只能从该入口进入 App。

### 4.1.1 下游扩展介入

`serve/app/routes/extensions.py::register_extension_routes(routes)` 是稳定扩展点：Base 中实现为空函数，下游 fork/clone 只在该函数中显式调用自己的 Manifest registrar。Base 发布不在该文件追加内容，避免每次升级都修改根入口。

```python
def register_extension_routes(routes: RouteRegistry) -> None:
    # Base intentionally leaves this empty.
    return None
```

下游不得再修改 `build_registry()`；它可在自己的 `routes/extensions/` 目录中按领域分片，但 `register_extension_routes()` 必须显式列出 registrar，不使用运行时自动扫描。

当单个 Base Manifest 超过 500 行时，将其拆为 `routes/<scope>/<domain>.py`；`routes/<scope>/__init__.py` 显式聚合分片。无论物理分片数量多少，`build_registry()` 与 Registry Catalog 仍是全局唯一视图。

### 4.2 Laravel 风格 DSL

```python
routes = RouteRegistry()

admin_public = routes.group(
    prefix="/api/admin",
    name="admin.",
    tags=["admin"],
    access=RouteAccess.PUBLIC,
)

admin = admin_public.group(
    middleware=[require_admin],
    access=RouteAccess.ADMIN,
)

users = admin.group(
    prefix="/user",
    name="user.",
    tags=["admin-user"],
)

users.get("/info", admin_user.user_info).name("info")
users.post("/logout", admin_user.logout).name("logout")
users.match(["GET", "POST"], "/probe", admin_user.probe).name("probe")
users.any("/callback", admin_user.callback).name("callback")
```

`RouteGroup` 必须提供：

```text
group(prefix, name, tags, middleware, access, responses, deprecated)
get(path, handler)
post(path, handler)
put(path, handler)
patch(path, handler)
delete(path, handler)
options(path, handler)
head(path, handler)
match(methods, path, handler)
any(path, handler)
fallback(path, handler)
crud(prefix, logic, permissions, tags, only, except_)
mount(path, app, name)
```

`RouteBuilder` 必须提供：

```text
name(route_id)
middleware(*policies)
without_middleware(*policies)
permission(*permissions)
tags(*tags)
response_model(model)
responses(mapping)
status_code(code)
summary(text)
description(text)
deprecated(bool)
include_in_schema(bool)
operation_id(value)
priority(value)
access(value)
```

### 4.3 DSL 精确语义

- Group 属性通过创建新不可变 `GroupSpec` 继承，不使用全局 context stack。
- `prefix` 做规范化连接：结果必须以 `/` 开头，非根路径不以 `/` 结尾，拒绝 `//`、`.` 和 `..` path segment。
- `name` 按 Group 到 Route 拼接，最终 Route ID 例如 `admin.user.info`；重复 ID 直接失败。
- Group `middleware` 按外层到内层、Route 自身的顺序编译为 `Depends(policy)`；精确相同的 callable 去重，保留首次出现顺序。
- Group 必须显式声明 `access=PUBLIC|AUTHENTICATED|ADMIN|CLIENT`；该字段进入最终 RouteSpec 和 Catalog，校验器不通过 Group 名称或 prefix 猜测安全边界。`RouteBuilder.access()` 只用于 legacy CRUD 同一资源混合 protected/public action 的兼容场景，仍须通过全局策略校验。
- `without_middleware()` 只能删除指定可调用对象，禁止“全部清空”；公开路由优先放入独立 Public Group，不通过个别豁免绕过 Admin Group。
- `permission("admin:user:list")` 编译为 `Depends(require_perms(...))`，并写入路由目录。
- `match()` 拒绝空 Method 集合、重复 Method 和未知 Method。
- `any()` 精确等于 `GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS`；由于可能抢占 CORS preflight，`/api/*` 下默认校验失败，只有 `.allow_options_override(True)` 后才允许。
- `fallback()` 只允许参数化或 path-converter 路径，自动标记 `RoutePriority.FALLBACK`，并在所有普通 HTTP 路由之后编译。
- 同一 priority 内保留 Manifest 声明顺序；编译器不暗中按字典序改变匹配顺序。
- `mount()` 统一进全局目录。编译/安装顺序固定为：普通 HTTP route → mount → fallback，避免根 `/{name}` 先匹配 `/uploads`。

### 4.4 RouteSpec 数据合同

`serve/app/routes/types.py::RouteSpec` 至少包含：

```python
@dataclass(frozen=True, slots=True)
class RouteSpec:
    methods: tuple[str, ...]
    path: str
    endpoint: Callable[..., Any]
    route_id: str
    access: RouteAccess
    middleware: tuple[Callable[..., Any], ...]
    permissions: tuple[str, ...]
    tags: tuple[str, ...]
    response_model: Any
    responses: Mapping[int | str, Mapping[str, Any]]
    status_code: int | None
    operation_id: str | None
    include_in_schema: bool
    deprecated: bool
    priority: RoutePriority
    source_file: str
    source_line: int
```

`RouteRegistry.compile_http_router()` 把上述 HTTP 字段显式映射到 `APIRouter.add_api_route()`，`RouteRegistry.install()` 另行安装 MountSpec；禁止用一个无类型 `**kwargs` 袋透传未校验参数。

## 5. Middleware 与 Handler 上下文

### 5.1 策略移出 Handler

当前 Handler 签名中的 `Depends(require_admin)`、`Depends(require_client)` 和 `Depends(require_perms(...))` 将移至 Route Group/Route Spec。

`serve/app/deps.py` 增加：

```python
def current_auth(request: Request) -> AuthInfo:
    auth = getattr(request.state, "auth", None)
    if auth is None:
        raise RuntimeError("route auth middleware is missing")
    return auth
```

`serve/app/deps.py::require_auth()` 在验证成功后执行：

```python
request.state.auth = auth_info
return auth_info
```

需要用到用户上下文的 Handler 改为：

```python
async def user_info(
    auth: AuthInfo = Depends(current_auth),
    db: AsyncSession = Depends(get_db),
):
    ...
```

`current_auth` 只取已由 Route middleware 写入的上下文，不做 Redis/DB 鉴权。这保证路由策略的唯一真源在 Manifest，同时保留 Handler 的类型化参数。

### 5.2 不得改变的语义

- `require_auth` 仍验证 Bearer token。
- `require_admin` 仍要求 `scope == "admin"`。
- `require_client` 仍要求 `scope == "client"`。
- `require_perms` 仍使用请求级 DB session 与缓存权限列表。
- FastAPI 相同 dependency callable 的请求级 cache 不得作为长期重复声明的借口；最终 Controller 不再声明鉴权/权限 dependency。
- Public Handler 不能注入 `current_auth`。

## 6. CRUD 介入方案

### 6.1 拆分当前 `crud_router()`

`serve/app/controllers/base.py::crud_router()` 当前混合了路由生成和 HTTP Handler 实现。重构为：

- `serve/app/controllers/crud.py::CrudController`
  - `get_list()`
  - `get_detail()`
  - `do_edit()`
  - `do_delete()`
  - `do_export()`
  - 内部保留现有 bind-user、BizError、导出队列和动态 create/edit 权限分支；
  - 不创建 `APIRouter`，不声明 URL。
- `serve/app/routes/resources.py::register_legacy_crud()`
  - 把一个 `CrudController` 显式注册为当前五个契约：
    - `GET /{module}/getList`
    - `GET /{module}/getDetail`
    - `POST /{module}/doEdit`
    - `POST /{module}/doDelete`
    - `POST /{module}/doExport`
  - 根据 `permissions` 生成 list/detail/create|edit/delete/export 权限元数据；
  - `only` / `except_` 在生成前计算，结果必须进全局目录。

`register_legacy_crud()` 必须显式承接现有工厂的全部兼容参数，不得只实现 Base 当前调用到的子集：

```python
def register_legacy_crud(
    group: RouteGroup,
    prefix: str,
    logic: BaseLogic,
    *,
    tags: list[str] | None = None,
    need_auth: bool = True,
    no_auth: list[str] | None = None,
    auth_dep: Callable[..., Any] | None = None,
    perms_prefix: str = "",
    actions: dict[str, list[str]] | None = None,
    only: list[str] | None = None,
    except_: list[str] | None = None,
) -> CrudController:
    ...
```

`need_auth/no_auth/auth_dep/actions` 在兼容期内保留原语义；其中 `actions` 在当前实现中只合并到局部变量、未影响路由行为，兼容层不得擅自赋予新语义。`CrudController` 通过 `request.state.auth` 读取可选 AuthInfo：已标记为 protected 的 action 如缺失 AuthInfo 则以路由配置错误失败，public action 传递 `user_id=None/is_super=False`。新增 `current_auth_optional(request)` 只用于这一兼容 Controller，普通受保护 Handler 仍使用硬失败的 `current_auth`。

Manifest 用法：

```python
admin.crud(
    "/user",
    logic=admin_user_logic,
    name="user",
    tags=["admin-user"],
    permissions="admin:user",
)
```

### 6.2 兼容层

- `serve/app/controllers/base.py` 在首个过渡版本中保留 `crud_router` import path，实现改为调用 `app.routes.legacy.crud_router()` 并发出 `DeprecationWarning`。
- Base 自身不再调用兼容 `crud_router()`。
- `serve/app/routes/legacy.py` 是唯一可以为旧下游生成 `APIRouter` 的兼容文件；必须标记删除版本。
- 移除兼容层属于 MAJOR 变更，不在本次首发中执行。

## 7. 现有路由的精确介入点

### 7.1 `serve/app/main.py`

| 当前内容 | 操作 | 目标 |
|---|---|---|
| 27 组 Controller import | 删除 | 只 import `app.routes.register_routes` |
| `health()` / `health_live()` / `health_ready()` | 移出 | `app.controllers.health` |
| 27 次 `app.include_router()` | 删除 | 一次 `register_routes(app)` |
| `/uploads` 目录构造和 `app.mount()` | 移出 | `app.routes.system.register_system_routes()` |
| 中间件、异常 Handler、lifespan | 保留 | 不改行为 |

### 7.2 `serve/app/routes/admin.py`

Admin 路由必须划分为两个明确 Group：

```python
admin_public = routes.group(
    prefix="/api/admin",
    name="admin.",
    access=RouteAccess.PUBLIC,
)
admin = admin_public.group(middleware=[require_admin], access=RouteAccess.ADMIN)
```

Public Group 只允许以下现有端点，不做默认扩大：

| Method | Path | Handler |
|---|---|---|
| GET | `/user/captcha` | `admin.user.get_captcha` |
| POST | `/user/login` | `admin.user.login` |
| POST | `/user/refreshToken` | `admin.user.refresh_token` |
| GET | `/setting/site` | `admin.setting.get_site_info` |

Protected Admin Group 按 Controller 的介入如下：

| Controller 文件 | 保留的 Handler 方法 | Route Manifest 操作 |
|---|---|---|
| `admin/user.py` | `user_info`, `change_password`, `logout`, `user_menus`, `user_role_ids`, `assign_roles`, `update_profile` | 声明 7 条 custom route + `admin.crud("/user", admin_user_logic, permissions="admin:user")` |
| `admin/setting.py` | `get_settings`, `set_settings`, `ai_provider_defaults`, `ai_review_defaults`, `generate_review_prompt`, `ai_test_connection` | 声明 6 条 custom route；`get/set` 权限从 Handler 移到 `.permission()` |
| `admin/log.py` | 无 custom Handler | 删除该 Controller 文件；Manifest 直接声明 `operationLog` 和 `loginLog` 两个 CRUD |
| `admin/menu.py` | `menu_tree` | 声明 custom route + `menu` CRUD |
| `admin/role.py` | `role_menu_ids`, `assign_menus` | 声明 2 条 custom route + `role` CRUD |
| `admin/message.py` | `unread_count`, `mark_read` | 声明 2 条 custom route + `message` CRUD |
| `admin/file.py` | `upload_file`, `upload_image`, `batch_delete` | 声明 3 条 custom route + `file` CRUD |
| `admin/dashboard.py` | `dashboard_stats`, `dashboard_system`, `dashboard_recent` | 声明 3 条 custom route |
| `admin/export.py` | `export_progress`, `export_download` | Group prefix `/export` 移到 Manifest，声明 2 条 route |
| `admin/article.py` | `ai_generate`, `collect_stream`, `gen_from_tags_stream`, `ai_rewrite_stream`, `collect_stats` | 声明 5 条 custom route + `article` CRUD |
| `admin/keyword.py` | `harvest_stream`, `poll_harvest_stream`, `bulk_approve`, `bulk_reject`, `bulk_set_stage`, `keyword_stats`, `ai_seed_suggest`, `ai_review_stream` | 声明 8 条 custom route + `keyword` CRUD |
| `admin/seo.py` | `dashboard`, `toggle_seo`, `set_kill_switch`, `rebuild_sitemap`, `list_sitemap_files`, `recompute_phase`, `indexnow_test`, `run_pipeline_now` | 声明 8 条 custom route + `publish_log` CRUD |
| `admin/dict.py` | 无 custom Handler | 删除该 Controller 文件；Manifest 直接声明 `dict` 和 `dict_item` 两个 CRUD |
| `admin/client_user.py` | `reset_password`, `kick` | 声明 2 条 custom route + `client_user` CRUD |
| `admin/task_monitor.py` | `list_tasks`, `trigger`, `queue_status` | 声明 3 条 custom route，移入 `_perm_list/_perm_trigger` |
| `admin/db_backup.py` | `manual_backup`, `download_backup` | 声明 2 条 custom route + `db_backup` CRUD |
| `admin/migration.py` | `migration_list`, `migration_run` | 声明 2 条 custom route，移入 `_perm_list/_perm_run` |
| `admin/monitor.py` | `monitor_metrics` | 声明 1 条 custom route，移入 `_perm_list` |
| `admin/import_api.py` | `import_template`, `import_upload` | 声明 2 条 custom route |
| `admin/session.py` | `session_list`, `session_kick` | 声明 2 条 custom route，移入 `_perm_list/_perm_kick` |
| `admin/cache.py` | `cache_stats`, `cache_clear` | 声明 2 条 custom route，移入 `_perm_stats/_perm_clear` |
| `admin/trash.py` | `trash_modules`, `trash_list`, `trash_restore`, `trash_purge` | 声明 4 条 custom route，移入 `_perm` 与 restore/purge 权限 |

`admin/file.py::proxy_private_file()` 不属于 `/api/admin` prefix；在同文件中用单独 Group 精确声明：

```python
private_file = routes.group(
    prefix="/api",
    name="private-file.",
    middleware=[require_admin],
    access=RouteAccess.ADMIN,
)
private_file.get("/file/{file_id}", admin_file.proxy_private_file).name("show")
```

现有 custom route 中具有细粒度 permission 的端点必须按下表原样迁移；未列出的 Admin custom route 只保留 `require_admin`，本次不得顺带新增权限门：

| Handler | Permission |
|---|---|
| `admin.cache.cache_stats` | `admin:cache:stats` |
| `admin.cache.cache_clear` | `admin:cache:clear` |
| `admin.client_user.reset_password` | `admin:client_user:edit` |
| `admin.client_user.kick` | `admin:client_user:edit` |
| `admin.db_backup.manual_backup` | `admin:db_backup:create` |
| `admin.db_backup.download_backup` | `admin:db_backup:list` |
| `admin.menu.menu_tree` | `admin:menu:list` |
| `admin.message.mark_read` | `admin:message:read` |
| `admin.migration.migration_list` | `admin:migration:list` |
| `admin.migration.migration_run` | `admin:migration:run` |
| `admin.monitor.monitor_metrics` | `admin:monitor:list` |
| `admin.role.role_menu_ids` | `admin:role:list` |
| `admin.role.assign_menus` | `admin:role:assignMenu` |
| `admin.session.session_list` | `admin:session:list` |
| `admin.session.session_kick` | `admin:session:kick` |
| `admin.setting.get_settings` | `admin:setting:get` |
| `admin.setting.set_settings` | `admin:setting:set` |
| `admin.task_monitor.list_tasks` | `admin:task_monitor:list` |
| `admin.task_monitor.queue_status` | `admin:task_monitor:list` |
| `admin.task_monitor.trigger` | `admin:task_monitor:trigger` |
| `admin.trash.trash_modules` | `admin:trash:list` |
| `admin.trash.trash_list` | `admin:trash:list` |
| `admin.trash.trash_restore` | `admin:trash:restore` |
| `admin.trash.trash_purge` | `admin:trash:purge` |
| `admin.user.user_role_ids` | `admin:user:list` |
| `admin.user.assign_roles` | `admin:user:assignRole` |

CRUD permission 由 `admin.crud(..., permissions=...)` 生成，必须保留当前特例：`dict` 与 `dict_item` 共用 `admin:dict`，`operationLog/loginLog` 分别使用 `admin:log:operation` / `admin:log:login`，`publish_log` 使用 `admin:seo`。

### 7.3 `serve/app/routes/client.py`

```python
client_public = routes.group(
    prefix="/api/client",
    name="client.",
    access=RouteAccess.PUBLIC,
)
client = client_public.group(
    middleware=[require_client],
    access=RouteAccess.CLIENT,
)
```

| Group | Method/Path | Handler |
|---|---|---|
| public | `POST /user/login` | `client.user.login` |
| public | `POST /user/register` | `client.user.register` |
| public | `POST /user/refreshToken` | `client.user.refresh_token` |
| protected | `GET /user/info` | `client.user.user_info` |
| protected | `POST /user/logout` | `client.user.logout` |
| protected | `GET /message/list` | `client.message.message_list` |
| protected | `GET /message/unread` | `client.message.message_unread` |
| protected | `POST /message/read` | `client.message.message_read` |
| protected | `POST /message/readAll` | `client.message.message_read_all` |

### 7.4 `serve/app/routes/public.py`

| Method | Path | Handler | Middleware |
|---|---|---|---|
| GET | `/api/dict/items` | `controllers.dict.get_items` | 无 |

### 7.5 `serve/app/routes/web.py`

| 类型 | Method | Path | Handler |
|---|---|---|---|
| normal | GET | `/sitemap.xml` | `web.seo.sitemap_index` |
| normal | GET | `/sitemap-{n}.xml` | `web.seo.sitemap_chunk` |
| normal | GET | `/robots.txt` | `web.seo.robots` |
| fallback | GET | `/{name}` | `web.seo.indexnow_key_file` |

`/{name}` 必须使用 `routes.fallback(...)`，不允许普通 `get()` 声明。

### 7.6 `serve/app/routes/system.py`

| 类型 | Method/Path | Handler/目标 |
|---|---|---|
| route | `GET /health` | `controllers.health.health` |
| route | `GET /health/live` | `controllers.health.health_live` |
| route | `GET /health/ready` | `controllers.health.health_ready` |
| mount | `/uploads` | `StaticFiles(storage/public)` |

`serve/app/routes/system.py::storage_public_path()` 使用与当前 `main.py` 相同的路径计算；`register_system_routes()` 在安装 MountSpec 前执行 `mkdir(parents=True, exist_ok=True)`。路径不进入 Settings API，不改当前存储契约。

## 8. Controller 逐文件改造规则

上述 27 个 Controller 模块统一执行以下机械变换：

1. 删除 `APIRouter` import。
2. 删除 `router = APIRouter(...)` 和 `file_proxy_router = ...`。
3. 删除所有 `@router.get/post/...` 与 `@file_proxy_router.*` 装饰器。
4. 删除所有 `router.include_router(crud_router(...))`。
5. 删除只为路由存在的 `_perm_* = require_perms(...)` 变量，在 Manifest 中声明对应 `.permission()`。
6. 需要 AuthInfo 数据的 Handler 使用 `Depends(current_auth)`；只把鉴权当前置条件使用、不读取 AuthInfo 的 Handler 删除 auth 参数。
7. `Depends(get_db)`、Request/Body/File/Form 解析和响应内容保持不变。
8. Handler 函数名、异步性、入参和返回行为不得顺带重写。

新增 `serve/tests/test_controller_route_boundary.py`，用 AST 扫描 `serve/app/controllers/**/*.py`，发现以下任一项即失败：

```text
APIRouter import
APIRouter(...) call
@router.* decorator
include_router(...) call
```

过渡兼容文件 `controllers/base.py` 只允许 re-export，不得自行构造 Router。

## 9. 路由目录与 CLI

### 9.1 命令

```bash
cd serve
python -m app.routes list
python -m app.routes list --scope admin --method POST --contains user
python -m app.routes json > /tmp/base-routes.json
python -m app.routes check
```

### 9.2 目录字段

```text
ROUTE_ID
METHODS
PATH
HANDLER
GROUP
ACCESS
MIDDLEWARE
PERMISSIONS
TAGS
RESPONSE_MODEL
OPERATION_ID
PRIORITY
SOURCE_FILE:LINE
```

`list` 的文本表格按 `PATH, METHODS, ROUTE_ID` 稳定排序；`json` 使用相同排序和固定 key 顺序，便于 diff。不得在目录中输出密钥、token 或 runtime 配置值。

## 10. 编译阶段强制校验

`serve/app/routes/validation.py::validate_registry()` 必须一次收集所有错误并以非零状态失败：

1. `(HTTP Method, normalized path)` 重复。
2. Route ID 重复或缺失。
3. `operation_id` 重复。
4. Handler 不可调用。
5. path 中声明的 parameter 在 Handler 签名中缺失。Handler 的其他必需参数可能是 Body/Query/Header，不能仅凭签名静态判为 path 错误。
6. `/api/admin/*` 没有显式 `access=PUBLIC|ADMIN`，或 `access=ADMIN` 但 effective middleware 不含 `require_admin/require_perms`。
7. `/api/client/*` 没有显式 `access=PUBLIC|CLIENT`，或 `access=CLIENT` 但 effective middleware 不含 `require_client`。
8. `.permission()` 的 RouteSpec 不是 `access=ADMIN`。
9. Fallback 不在 FALLBACK priority，或它可遮蔽同优先级路由。
10. 动态路径在同一 Method/层级遮蔽后续静态路径，例如 `/{id}` 早于 `/stats`。
11. `any()` 在 `/api/*` 下未显式允许 OPTIONS override。
12. RouteSpec 中存在未知内部标志；不提供公开的无类型 FastAPI route option 透传入口。
13. Mount 名称或 path 重复。

## 11. 分阶段实施顺序

### 阶段 0：冻结可观测契约

**文件**：

- 新增 `serve/tests/fixtures/route-catalog-v1.json`
- 新增 `serve/tests/test_route_contract.py`

**方法**：

- `snapshot_openapi_routes(app)`：从 `app.openapi()["paths"]` 提取 Method、path、operationId、tags、response schema。
- `test_current_route_contract_snapshot()`：确认当前 159 operations 与 fixture 完全相等。
- 建立 Public/Admin/Client 策略清单，作为新 Registry 元数据对照基线。

**出口条件**：不修改任何路由即可稳定生成 159 条基线。

### 阶段 1：建立 Registry/DSL，尚不接管 App

**文件**：

- 新增 `serve/app/routes/types.py`
- 新增 `serve/app/routes/registry.py`
- 新增 `serve/app/routes/catalog.py`
- 新增 `serve/app/routes/validation.py`
- 新增 `serve/tests/test_route_registry.py`

**核心方法**：

- `RouteRegistry.group()` / `compile_http_router()` / `install()` / `validate()` / `catalog()`
- `RouteGroup.group()` / HTTP verb methods / `match()` / `any()` / `fallback()` / `mount()`
- `RouteBuilder` 的元数据链式方法
- `validate_registry()`

**出口条件**：DSL 单元测试覆盖继承、去重、碰撞、fallback、any/CORS 和稳定目录。

### 阶段 2：拆分 CRUD Handler 与路由生成

**文件**：

- 新增 `serve/app/controllers/crud.py`
- 新增 `serve/app/routes/resources.py`
- 新增 `serve/app/routes/legacy.py`
- 修改 `serve/app/controllers/base.py`
- 新增 `serve/tests/test_crud_routes.py`

**方法**：

- 把 `_do_get_list/_do_get_detail/_do_edit/_do_delete/_do_export` 行为移入 `CrudController`。
- `register_legacy_crud()` 生成五个 RouteSpec，不生成隐藏 Controller Router。
- 使用现有 CRUD 行为测试验证 bind-user、权限、导出和 BizError 契约不变。

### 阶段 3：建立全量 Manifest 和影子对照

**文件**：

- 新增 `serve/app/routes/system.py`
- 新增 `serve/app/routes/admin.py`
- 新增 `serve/app/routes/client.py`
- 新增 `serve/app/routes/public.py`
- 新增 `serve/app/routes/web.py`
- 新增 `serve/app/routes/extensions.py`
- 新增 `serve/app/routes/__init__.py`
- 新增 `serve/app/routes/__main__.py`

**方法**：

- `build_registry()` 注册全部 159 条当前 operation，但 `main.py` 仍使用旧 Router。
- `compare_catalogs(legacy_openapi, registry_catalog)` 对比 Method/path/operationId/tags/response schema。
- 新 Registry 任何多路由、少路由或契约差异都阻塞切换。

### 阶段 4：切换唯一入口并去装饰器

**文件**：

- 修改 `serve/app/main.py`
- 修改 `serve/app/deps.py`
- 新增 `serve/app/controllers/health.py`
- 修改第 7/8 节列出的全部 Controller
- 新增 `serve/tests/test_controller_route_boundary.py`

**切换顺序**：

1. 先让 `require_auth` 写入 `request.state.auth`，并测试 `current_auth` 的成功/缺失策略分支。
2. Controller 鉴权参数改用 `current_auth`，同时移除装饰器与 Router 对象。
3. `main.py` 切换为唯一 `register_routes(app)`。
4. 立即执行 Registry `check`、OpenAPI snapshot 和鉴权契约测试；失败则不允许保留半切换状态。

### 阶段 5：文档、发布与下游兼容

**文件**：

- 修改 `serve/README.md`
- 修改 `serve/docs/api-convention.md`
- 修改 `AGENTS.md` 和 `CLAUDE.md` 的架构图/规则
- 修改 `VERSION`、`CHANGELOG.md`、`admin/package.json`、`admin/package-lock.json`
- 修改 `UPSTREAM.md`

**发布要求**：

- 本重构会移除各 Controller 模块对外暴露的 `router` / `file_proxy_router` 对象，即使 HTTP/OpenAPI 契约不变、`controllers.base.crud_router` 保留兼容 import，Python 集成面仍是不兼容变更；首次发布必须使用 MAJOR SemVer。
- 只有在实施前提供并验证所有 Controller router 对象的完整兼容层，且下游不需要修改集成代码时，才可重新评估 MINOR；当前方案不做该假设。
- 任何改变 URL/Method/operationId 的差异必须作为独立 API 变更评审，不借路由重构顺带放行。
- Changelog 必须写明新 Route DSL、兼容层移除版本、冲突热点、同步和回滚。

## 12. 验收合同与可复现证据

### 12.1 静态边界

```bash
cd serve
! grep -RInE 'APIRouter|@router\.|include_router\(' app/controllers \
  --include='*.py' --exclude='base.py'
```

AST 测试是正式验收，`grep` 只是人工复核证据。

### 12.2 路由完整性

```bash
cd serve
.venv/bin/python -m app.routes check
.venv/bin/python -m app.routes json > /tmp/base-routes.json
.venv/bin/python -m pytest -q tests/test_route_registry.py \
  tests/test_route_contract.py tests/test_crud_routes.py \
  tests/test_controller_route_boundary.py
```

必须证明：

- Registry 编译后仍为 159 个 OpenAPI operation / 159 个 path；
- Method/path/operationId/tags/response schema 与基线 fixture 零差异；
- 公开、Admin、Client 和 permission 策略目录零差异；
- `/{name}` 总是最后的 HTTP fallback，`/health*`、`/robots.txt`、`/sitemap*` 不被遮蔽；
- `/uploads` Mount 仍可达。

### 12.3 行为与全量验证

```bash
cd serve && .venv/bin/python -m pytest -q
cd serve && .venv/bin/alembic upgrade head
cd admin && npm run lint && npm run build
python3 scripts/check-base-release.py
git diff --check
```

必须新增的代表性行为测试：

1. Public route 不携 token 可达。
2. Admin route 不携 token 返回现有 401 信封。
3. Client token 访问 Admin route 返回现有 403 信封。
4. 普通 Admin 无 permission 返回现有 403 信封。
5. `current_auth` 只从已执行 middleware 获取 AuthInfo，缺失时以配置错误失败。
6. CRUD list/detail/edit/delete/export 各有一条端到端路由测试。
7. `route list/json` 两次运行字节级相同。

## 13. 阻塞项、非目标和依赖

### 13.1 当前阻塞项

- 2026-08-17 checkpoint 审计已通过，当前无方案级 P0 blocker。
- 实施开始前必须先生成并提交 159 条当前路由契约 fixture，不得凭文档手工回忆契约。

### 13.2 依赖

- 现有 FastAPI `APIRouter.add_api_route()` 和 `Depends()`。
- 现有 `require_auth/require_admin/require_client/require_perms`。
- 现有 `BaseLogic`、数据库 dependency 和响应信封。
- 不新增第三方路由框架依赖。

### 13.3 非目标

- 不将现有 RPC 风格 URL 改为 REST。
- 不合并或拆分业务 Handler、Logic 或 Model。
- 不改变操作日志中间件、CORS、异常信封和 token 系统。
- 不同时重构前端 Vue Router/菜单。
- 不为具体产品增加路由。
- 不使用运行时自动扫描 Controller 来重新制造隐式注册。

## 14. 风险与回滚

| 风险 | 防护 |
|---|---|
| 漏注册或多注册路由 | 159 条 snapshot + Registry/OpenAPI 差异门 |
| 鉴权/权限移动后暴露接口 | Admin/Client/Public 显式 Group + policy catalog + 代表性 401/403 测试 |
| `current_auth` 执行顺序错误 | route-level dependency 顺序测试 + 缺失 middleware 硬失败 |
| Catch-all 遮蔽系统路由 | `fallback()` 专用 API + priority 校验 |
| OpenAPI/SDK 契约漂移 | operationId/tags/schema snapshot；差异必须显式批准 |
| 下游仍 import `controllers.base.crud_router` | 一个过渡版本兼容 shim + DeprecationWarning + Changelog |
| 一次性大切换难以定位 | 先影子生成 Catalog，零差异后再切 App 入口 |

回滚单位：

1. 切换前 Tag/提交作为回滚点。
2. 如新 Registry 实施失败，整体回退 `main.py`、Controller、`deps.py` 与新 `routes/` 目录，不做半切换。
3. 本重构不包含 DB migration，回滚不涉及数据修复。
4. 已发布 Base Tag 不移动；回滚以新 SemVer 修复版本发布。

## 15. 实施完成定义

只有同时满足以下条件才能标记完成：

- `main.py` 只有一个路由注册入口。
- Base Controller 零 `APIRouter`、零路由装饰器、零 `include_router()`。
- 全部 159 条当前 operation 在 Registry Catalog 中可查，含 Handler、middleware、permission 和源位置。
- 新旧 HTTP/OpenAPI 契约零未批准差异。
- Registry 校验、路由专项测试、后端全量测试、Alembic head、前端 lint/build、release check 和 `git diff --check` 全部通过。
- `serve/README.md`、`api-convention.md`、`CHANGELOG.md` 和 `UPSTREAM.md` 与实际 DSL/兼容期一致。
- 发布时写明这一能力为何可被多个下游通用复用：它统一路由注册、策略继承、目录和校验，不包含产品规则。

## 16. Checkpoint 审计记录（2026-08-17）

### 16.1 审计范围与证据

本次为用户明确要求的方案 checkpoint 审计，检查问题定义、现有路由完整性、文件/方法介入、鉴权与权限边界、CRUD 兼容、注册顺序、下游扩展、SemVer、验收和回滚。

复现证据：

- `app.openapi()`：159 paths / 159 operations，其中 GET 70、POST 89。
- AST 盘点：27 个存在路由职责的 Controller 文件，100 个源码路由装饰函数，14 处 Controller `include_router()`。
- 契约算术：86 个 custom Controller endpoint + 14 个 CRUD × 5 endpoint + 3 个 `main.py` health endpoint = 159。
- 权限核对：Controller 中 23 个唯一 `require_perms()` 字面量均已进入本文档的 custom/CRUD permission 映射；共用 permission 的多个 Handler 分别列明。
- 文档静态验证：`git diff --check` 通过。

### 16.2 审计发现与修正

| ID | 初始级别 | 发现 | 修正 | 状态 |
|---|---|---|---|---|
| A-01 | P0 | 原稿只聚合 Base Manifest，下游新增路由必须继续修改根入口 | 新增稳定 `routes/extensions.py::register_extension_routes()` 介入点和显式分片合同 | 已关闭 |
| A-02 | P0 | 原稿把 Mount 放在 fallback 之后，`/{name}` 可先匹配 `/uploads` | 安装顺序改为普通 HTTP → Mount → Fallback | 已关闭 |
| A-03 | P0 | 原稿 CRUD 新工厂未覆盖 `need_auth/no_auth/auth_dep/actions` 的下游兼容面 | 补齐 `register_legacy_crud()` 精确签名、可选 AuthInfo 行为和 `actions` 当前无效语义 | 已关闭 |
| A-04 | P0 | 原稿把 HTTP 契约不变时的发布评为 MINOR，忽略 Controller `router` Python 集成面被删除 | 首发固定为 MAJOR，除非实施前证明完整兼容层 | 已关闭 |
| A-05 | P0 | 原稿只通过 prefix/middleware callable 推断访问边界，Catalog 无显式安全分类 | 新增 `RouteAccess` 必填字段并加入 Admin/Client/Public/permission 校验 | 已关闭 |

### 16.3 审计结论

**CHECKPOINT_PASS**

- 开放 P0 blocker：0。
- 方案可以进入第 11 节阶段 0，但必须先冻结 159 条可观测契约，不允许直接从 Controller 拆装饰器开始。
- 本次审计是方案 checkpoint，不是代码 postwork 审计；实施完成后仍需执行第 12 节工程验证。

## 17. 实施检查记录（2026-08-18）

### 17.1 已发现并修正

| ID | 级别 | 发现 | 修正 | 状态 |
|---|---|---|---|---|
| I-01 | P0 | `SOURCE_FILE:LINE` 跳过整个 `app/routes/`，Catalog 来源退化为 `runpy`/`stdin`，且绝对路径不利于跨工作树 diff | 只跳过 Registry/CRUD 内部工厂，在 Builder 创建时冻结调用帧，并规范化为 `serve` 相对路径 | 已关闭 |
| I-02 | P0 | 嵌套 Group middleware 覆盖外层策略 | 改为外层 → 内层继承，Route 提交时按 callable identity 去重 | 已关闭 |
| I-03 | P0 | 公开 `route_option()` 可覆盖 FastAPI `dependencies/methods` 等受控字段 | 移除无类型透传入口，内部标志仅允许 `from_any/allow_options_override` | 已关闭 |
| I-04 | P0 | access 校验只看 URL prefix 且按函数名匹配，可漏掉 `/api/file/*` 或被同名函数伪装 | 按显式 RouteAccess 全局校验，并对 `require_auth/admin/client` 使用 callable identity | 已关闭 |
| I-05 | P1 | Catalog 缺少 ACCESS，未显式设置 operationId 时输出空值 | 增加 ACCESS，并从编译后 APIRoute 读取最终 `unique_id` | 已关闭 |
| I-06 | P1 | 设计文档称只编译一个 APIRouter，与 Mount 前于 fallback 的实现条件矛盾 | 明确 normal router → Mount → fallback router 的两段安装模型 | 已关闭 |
| I-07 | P1 | legacy CRUD 的 `name` 参数未参与 Route ID；混合 public/protected action 无法同时表达 access 与 middleware | Route ID 使用 name；增加受校验的 route-level access，并对 public action 精确移除继承策略 | 已关闭 |
| I-08 | P1 | Manifest 在测试收集期提前导入 Controller，使离线 Redis fixture 未覆盖模块级 `from ... import` 绑定；新环境也未声明 `aiosqlite` | fixture 同步覆盖真实调用点，并在 `requirements-dev.txt` 声明 SQLite async 驱动 | 已关闭 |

### 17.2 当前验收状态

- Registry check：159 HTTP routes / 1 mount。
- Registry Catalog：160 entries，operationId 无空值，来源全部指向 `app/routes/` Manifest。
- 当前 App OpenAPI 与冻结 fixture：159 operations / 159 paths 零差异。
- 路由专项 77 passed；后端全量 289 passed；前端 lint 0 errors / build PASS。
- v2.0.0 当次 Alembic 验收曾使用临时 `base_verify`，且未对旧共享库执行 stamp 或改写；v3.0.0 已删除临时库并统一到受 ACL 隔离的 `base_platform_app@base_platform`（见 `database-boundary.md`）。
- release metadata 与 `git diff --check` 通过；发布提交绑定 immutable tag `base/v2.0.0`。
