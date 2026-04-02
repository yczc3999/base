# Admin 前端设计文档

## 一、技术栈

| 项 | 选型 |
|----|------|
| 框架 | Vue 3 + TypeScript |
| 构建 | Vite |
| UI | Element Plus |
| 状态 | Pinia |
| 路由 | Vue Router 4 |
| 请求 | Axios |
| 图标 | Lucide Icons |
| 样式 | SCSS + CSS Variables |

---

## 二、目录结构

```
admin/
├── src/
│   ├── api/                        # API 层
│   │   ├── request.ts              #   Axios 封装（token / loading / 错误拦截 / 重试）
│   │   ├── types.ts                #   通用响应类型
│   │   └── modules/                #   按模块拆分
│   │       ├── auth.ts             #     登录 / 登出 / 续期
│   │       ├── user.ts             #     用户管理
│   │       ├── role.ts             #     角色管理
│   │       ├── menu.ts             #     菜单管理
│   │       ├── setting.ts          #     系统配置
│   │       ├── message.ts          #     系统消息
│   │       └── log.ts              #     操作日志 / 登录日志
│   │
│   ├── components/                 # 公共组件
│   │   ├── CrudTable/              #   声明式 CRUD 表格（搜索 + 表格 + 分页 + 权限按钮）
│   │   │   ├── index.vue           #     主组件
│   │   │   ├── SearchBar.vue       #     搜索栏（根据配置自动渲染）
│   │   │   ├── TableColumn.vue     #     列渲染器
│   │   │   └── types.ts            #     类型定义
│   │   ├── CrudForm/               #   声明式 CRUD 表单（弹窗 / 页面双模式）
│   │   │   ├── DialogForm.vue      #     弹窗模式
│   │   │   ├── PageForm.vue        #     页面模式
│   │   │   ├── FormRenderer.vue    #     表单渲染器（根据配置生成表单项）
│   │   │   └── types.ts            #     类型定义
│   │   ├── PermButton/             #   权限按钮（根据 permissions 自动显隐）
│   │   │   └── index.vue
│   │   ├── IconPicker/             #   图标选择器（菜单管理用）
│   │   │   └── index.vue
│   │   ├── DictTag/                #   字典标签（状态、类型等枚举渲染）
│   │   │   └── index.vue
│   │   ├── ImageUpload/            #   图片上传（头像等）
│   │   │   └── index.vue
│   │   └── TreeSelect/             #   树形选择器（菜单父级、部门等）
│   │       └── index.vue
│   │
│   ├── layouts/                    # 布局
│   │   ├── default/                #   后台主布局
│   │   │   ├── index.vue           #     布局容器（Sidebar + Header + Tags + Content）
│   │   │   ├── Sidebar.vue         #     侧边栏（菜单树渲染）
│   │   │   ├── Header.vue          #     顶栏（面包屑 + 用户菜单 + 消息铃铛）
│   │   │   ├── TagsView.vue        #     标签栏（多页签）
│   │   │   └── AppMain.vue         #     内容区（router-view + keep-alive）
│   │   └── blank/                  #   空白布局（登录页）
│   │       └── index.vue
│   │
│   ├── router/                     # 路由
│   │   ├── index.ts                #   路由实例
│   │   ├── guard.ts                #   路由守卫（鉴权 + 动态路由加载）
│   │   ├── static.ts               #   静态路由（登录 / 404 / 重定向）
│   │   └── dynamic.ts              #   动态路由生成（后端菜单 → Vue 路由）
│   │
│   ├── stores/                     # Pinia 状态
│   │   ├── user.ts                 #   用户信息 + token + 权限列表
│   │   ├── app.ts                  #   应用设置（侧边栏折叠、主题色、语言）
│   │   ├── permission.ts           #   路由权限（动态路由）
│   │   └── tags.ts                 #   标签栏状态
│   │
│   ├── hooks/                      # 组合式函数（核心复用逻辑）
│   │   ├── useCrud.ts              #   CRUD 完整逻辑（列表 + 搜索 + 分页 + 增删改查）
│   │   ├── usePermission.ts        #   权限判断（hasPerms / hasRole）
│   │   ├── useTable.ts             #   表格逻辑（分页 + 排序 + 选择）
│   │   └── useForm.ts              #   表单逻辑（校验 + 提交 + 重置）
│   │
│   ├── utils/                      # 工具
│   │   ├── auth.ts                 #   Token 存取（localStorage）
│   │   ├── storage.ts              #   存储封装
│   │   ├── dict.ts                 #   字典工具（状态映射等）
│   │   └── index.ts                #   通用工具
│   │
│   ├── styles/                     # 样式
│   │   ├── variables.scss          #   CSS 变量（主题色、间距、字号）
│   │   ├── reset.scss              #   重置样式
│   │   ├── layout.scss             #   布局样式
│   │   └── index.scss              #   入口
│   │
│   ├── views/                      # 页面（每个模块一个目录）
│   │   ├── login/
│   │   │   └── index.vue           #   登录页
│   │   ├── dashboard/
│   │   │   └── index.vue           #   仪表盘
│   │   ├── system/
│   │   │   ├── user/
│   │   │   │   ├── index.vue       #   用户列表
│   │   │   │   ├── edit.vue        #   编辑（弹窗 或 页面，由配置决定）
│   │   │   │   └── detail.vue      #   详情
│   │   │   ├── role/
│   │   │   │   ├── index.vue
│   │   │   │   └── edit.vue
│   │   │   ├── menu/
│   │   │   │   ├── index.vue
│   │   │   │   └── edit.vue
│   │   │   ├── setting/
│   │   │   │   └── index.vue
│   │   │   └── log/
│   │   │       ├── operation/
│   │   │       │   └── index.vue
│   │   │       └── login/
│   │   │           └── index.vue
│   │   ├── profile/
│   │   │   └── index.vue           #   个人中心
│   │   └── message/
│   │       └── index.vue           #   系统消息
│   │
│   ├── App.vue
│   └── main.ts
│
├── .env                            # 开发环境
├── .env.production                 # 生产环境
├── index.html
├── package.json
├── vite.config.ts
└── tsconfig.json
```

---

## 三、核心设计

### 3.1 request.ts — Axios 封装

```typescript
// 功能清单：
// 1. baseURL 从 .env 读取
// 2. 请求拦截器：自动注入 Authorization: Bearer token
// 3. 响应拦截器：统一处理 code !== 0 的错误
// 4. 401 自动跳转登录（清除 token + 路由跳转）
// 5. 401 时自动尝试 refresh_token 续期（透明重试）
// 6. loading 控制：默认显示全屏 loading，可通过参数关闭
// 7. 请求取消：页面切换时取消未完成请求
// 8. 错误提示：ElMessage 统一提示

interface RequestOptions {
  loading?: boolean        // 是否显示 loading（默认 true）
  showError?: boolean      // 是否弹出错误提示（默认 true）
  retry?: boolean          // 401 时是否尝试 refresh（默认 true）
}

// 用法：
// import { get, post } from '@/api/request'
// const data = await get('/user/getList', { page: 1 })
// const data = await post('/user/doEdit', { username: 'test' }, { loading: false })
```

### 3.2 CrudTable — 声明式表格

```vue
<!-- 用法：一个配置对象生成完整列表页 -->
<CrudTable
  :api="userApi"
  :columns="columns"
  :search="searchFields"
  :perms="'admin:user'"
  :edit-mode="'dialog'"
  @edit="handleEdit"
/>
```

**CrudTable 自动处理：**
- 搜索栏渲染（根据 search 配置自动生成 input / select / datePicker 等）
- 表格渲染（根据 columns 配置自动生成列，支持自定义插槽）
- 分页（自动管理 page / pageSize / total）
- 排序（点击表头排序，传 sortField / sortOrder）
- 工具栏按钮（新增 / 删除 / 导出，根据 perms 自动显隐）
- 行操作按钮（编辑 / 删除 / 详情，根据 perms 自动显隐）
- 批量选择 + 批量删除
- keyword 搜索

### 3.3 CrudForm — 声明式表单（弹窗 / 页面双模式）

```typescript
// 配置
const formFields = [
  { field: 'username', label: '用户名', type: 'input', rules: [{ required: true, min: 3 }] },
  { field: 'password', label: '密码', type: 'password', rules: [{ required: true, min: 6 }], showOnCreate: true },
  { field: 'nickname', label: '昵称', type: 'input' },
  { field: 'email', label: '邮箱', type: 'input' },
  { field: 'phone', label: '手机号', type: 'input' },
  { field: 'status', label: '状态', type: 'switch', default: 1 },
  { field: 'avatar', label: '头像', type: 'imageUpload' },
  { field: 'role_ids', label: '角色', type: 'select', multiple: true, dictApi: roleApi.getList },
]
```

**双模式切换：**
```vue
<!-- 弹窗模式（默认）—— 适合简单表单 -->
<CrudForm mode="dialog" :fields="formFields" :api="userApi" />

<!-- 页面模式 —— 适合复杂表单 -->
<CrudForm mode="page" :fields="formFields" :api="userApi" />
```

弹窗模式：点编辑 → ElDialog 弹出 → 表单 → 提交 → 关闭刷新列表
页面模式：点编辑 → 路由跳转到 edit.vue → 表单 → 提交 → 返回列表

### 3.4 PermButton — 权限按钮

```vue
<!-- 有 admin:user:create 权限才显示 -->
<PermButton perms="admin:user:create" type="primary" @click="handleAdd">
  新增
</PermButton>

<!-- 多权限：满足任一即显示 -->
<PermButton :perms="['admin:user:edit', 'admin:user:create']">
  编辑
</PermButton>
```

内部实现：从 userStore.permissions 中匹配，无权限则不渲染（v-if，不是 v-show）。

### 3.5 动态路由

```
登录成功
  → 存储 token
  → 调 /user/menus 获取菜单 + 权限
  → 菜单树 → 生成 Vue Router 路由（template_path → 动态 import 组件）
  → 权限列表 → 存入 userStore.permissions
  → 跳转首页
```

**template_path 映射规则：**
```
后端返回: template_path = "system/user/index"
前端映射: () => import('@/views/system/user/index.vue')
```

不硬编码路由，全部从后端菜单动态生成。静态路由只有：login / 404 / redirect。

---

## 四、页面规范

### 4.1 列表页（index.vue）

每个列表页的结构：
```
┌─────────────────────────────────────┐
│ 搜索栏（keyword + 筛选条件 + 搜索/重置）│
├─────────────────────────────────────┤
│ 工具栏（新增按钮 + 批量删除 + 导出）    │
├─────────────────────────────────────┤
│ 表格（数据列 + 操作列）               │
├─────────────────────────────────────┤
│ 分页                                │
└─────────────────────────────────────┘
```

用 CrudTable 一个组件搞定，页面代码只有配置 + 自定义插槽。

### 4.2 编辑页（edit.vue）

两种模式：
- **弹窗模式**：不需要 edit.vue，CrudForm 以 Dialog 形式在 index.vue 中弹出
- **页面模式**：独立 edit.vue，通过路由 `/system/user/edit/:id?` 访问

什么时候用弹窗，什么时候用页面：
- 表单字段 ≤ 8 个 → 弹窗
- 表单字段 > 8 个 或 有复杂交互（富文本、多 Tab）→ 页面
- 由 CrudTable 的 `edit-mode` 属性控制，默认弹窗

### 4.3 详情页（detail.vue）

只读展示，用 ElDescriptions 组件。
简单场景用弹窗，复杂场景用页面。

---

## 五、API 模块规范

每个 API 模块文件统一导出一个对象：

```typescript
// api/modules/user.ts
import { get, post } from '../request'

export default {
  getList:    (params: any) => get('/admin/user/getList', params),
  getDetail:  (id: number)  => get('/admin/user/getDetail', { id }),
  doEdit:     (data: any)   => post('/admin/user/doEdit', data),
  doDelete:   (ids: any)    => post('/admin/user/doDelete', { ids }),
  // 自定义接口
  login:      (data: any)   => post('/admin/user/login', data, { loading: true, showError: true }),
  menus:      ()            => get('/admin/user/menus', {}, { loading: false }),
  info:       ()            => get('/admin/user/info'),
  assignRoles:(data: any)   => post('/admin/user/assignRoles', data),
}
```

**规则：**
- 文件名 = 后端模块名
- CRUD 四个方法名固定：getList / getDetail / doEdit / doDelete
- 自定义方法名和后端一致
- 默认显示 loading，调用方可覆盖

---

## 六、状态管理

### 6.1 userStore

```typescript
// stores/user.ts
{
  token: string           // access_token
  refreshToken: string    // refresh_token
  userInfo: {}            // 用户信息
  permissions: string[]   // 权限标识列表 ["admin:user:list", ...]
  roles: string[]         // 角色列表 ["admin", ...]
}
```

### 6.2 permissionStore

```typescript
// stores/permission.ts
{
  menus: []               // 后端返回的菜单树（渲染侧边栏用）
  routes: []              // 动态生成的 Vue Router 路由
  isLoaded: boolean       // 是否已加载
}
```

### 6.3 appStore

```typescript
// stores/app.ts
{
  sidebarCollapsed: boolean   // 侧边栏是否折叠
  theme: 'light' | 'dark'    // 主题
  primaryColor: string        // 主题色
  language: string            // 语言
  size: string                // 组件尺寸
}
```

### 6.4 tagsStore

```typescript
// stores/tags.ts
{
  visitedViews: []        // 已访问的页面（标签栏）
  cachedViews: []         // 缓存的页面（keep-alive）
}
```

---

## 七、鉴权流程

```
用户访问页面
  │
  ├→ 路由守卫检查 token
  │     ├→ 无 token → 跳登录页
  │     └→ 有 token
  │           ├→ 已加载路由 → 放行
  │           └→ 未加载路由
  │                 ├→ 调 /user/menus
  │                 ├→ 生成动态路由 + 存储权限
  │                 └→ addRoute → 放行
  │
  ├→ 请求拦截器自动注入 token
  │
  ├→ 响应拦截器
  │     ├→ code=0 → 返回 data
  │     ├→ code=401 → 尝试 refresh
  │     │     ├→ 成功 → 重试原请求
  │     │     └→ 失败 → 清除 token → 跳登录页
  │     └→ 其他 → ElMessage 提示 msg
  │
  └→ 页面级权限
        ├→ PermButton 根据 permissions 控制按钮显隐
        └→ v-permission 自定义指令（可选）
```

---

## 八、useCrud 核心 Hook

```typescript
// hooks/useCrud.ts
// 封装完整的 CRUD 逻辑，页面只需传配置

const {
  // 状态
  loading,          // 加载中
  tableData,        // 表格数据
  total,            // 总数
  queryParams,      // 查询参数（page, pageSize, filters, keyword）
  formData,         // 表单数据
  formVisible,      // 弹窗是否显示
  formMode,         // 'create' | 'edit'

  // 方法
  getList,          // 刷新列表
  handleSearch,     // 搜索
  handleReset,      // 重置搜索
  handleAdd,        // 新增（打开空表单）
  handleEdit,       // 编辑（打开带数据的表单）
  handleDelete,     // 删除
  handleBatchDelete,// 批量删除
  handleSubmit,     // 提交表单
  handlePageChange, // 翻页
  handleSortChange, // 排序
} = useCrud({
  api: userApi,                   // API 模块
  queryParams: { status: 1 },     // 默认查询参数
  immediate: true,                // 是否立即查询
})
```

---

## 九、环境变量

```
# .env
VITE_APP_TITLE=Base Admin
VITE_API_BASE_URL=http://localhost:3000
VITE_API_PREFIX=/api

# .env.production
VITE_APP_TITLE=Base Admin
VITE_API_BASE_URL=https://api.example.com
VITE_API_PREFIX=/api
```

---

## 十、实现顺序

```
Phase 1: 基础设施
  1. Vite 项目初始化
  2. request.ts（Axios 封装）
  3. stores（user / app / permission / tags）
  4. router（静态路由 + 守卫 + 动态路由生成）
  5. layouts（default 布局 + Sidebar + Header + TagsView）
  6. 登录页

Phase 2: 公共组件
  7. PermButton
  8. DictTag
  9. CrudTable（SearchBar + Table + Pagination）
  10. CrudForm（Dialog + Page 双模式）
  11. useCrud hook

Phase 3: 业务页面
  12. Dashboard
  13. 用户管理（CRUD + 分配角色）
  14. 角色管理（CRUD + 分配权限）
  15. 菜单管理（树形 CRUD）
  16. 系统配置
  17. 日志查看
  18. 系统消息
  19. 个人中心
```
