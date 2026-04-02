# Admin 前端 — 资产清单

> Vue 3 + TypeScript + Element Plus + Vite
> 目标：世界级后台管理系统 — 视觉、交互、动效、配色均达到顶级水准

---

## 技术栈

| 项 | 选型 | 版本 |
|----|------|------|
| 框架 | Vue 3 (Composition API) | 3.5+ |
| 语言 | TypeScript | 5.x |
| 构建 | Vite | 6.x |
| UI 库 | Element Plus | 2.9+ |
| 状态 | Pinia | 2.x |
| 路由 | Vue Router | 4.x |
| HTTP | Axios | 1.x |
| 图标 | Lucide Vue Next | latest |
| 样式 | SCSS + CSS Variables | — |
| 动效 | @vueuse/motion / CSS Transitions | — |
| 图表 | ECharts | 5.x |

---

## 目录结构

```
admin/
├── src/
│   ├── api/                            # API 层
│   │   ├── request.ts                  #   Axios 封装
│   │   ├── types.ts                    #   通用类型
│   │   └── modules/                    #   按模块
│   │       ├── auth.ts
│   │       ├── user.ts
│   │       ├── role.ts
│   │       ├── menu.ts
│   │       ├── setting.ts
│   │       ├── message.ts
│   │       └── log.ts
│   │
│   ├── components/                     # 公共组件
│   │   ├── CrudTable/                  #   声明式 CRUD 表格
│   │   ├── CrudForm/                   #   声明式表单（弹窗/页面）
│   │   ├── PermButton/                 #   权限按钮
│   │   ├── DictTag/                    #   字典标签
│   │   ├── IconPicker/                 #   图标选择器
│   │   ├── ImageUpload/                #   图片上传
│   │   ├── TreeSelect/                 #   树形选择器
│   │   ├── PageHeader/                 #   页面标题栏
│   │   └── StatusDot/                  #   状态指示点
│   │
│   ├── layouts/                        # 布局
│   │   ├── default/                    #   后台主布局
│   │   │   ├── index.vue
│   │   │   ├── Sidebar.vue
│   │   │   ├── Header.vue
│   │   │   ├── TagsView.vue
│   │   │   └── AppMain.vue
│   │   └── blank/                      #   空白布局
│   │       └── index.vue
│   │
│   ├── router/                         # 路由
│   │   ├── index.ts
│   │   ├── guard.ts
│   │   ├── static.ts
│   │   └── dynamic.ts
│   │
│   ├── stores/                         # Pinia 状态
│   │   ├── user.ts
│   │   ├── app.ts
│   │   ├── permission.ts
│   │   └── tags.ts
│   │
│   ├── hooks/                          # 组合式函数
│   │   ├── useCrud.ts
│   │   ├── usePermission.ts
│   │   ├── useTable.ts
│   │   └── useForm.ts
│   │
│   ├── utils/                          # 工具
│   │   ├── auth.ts
│   │   ├── storage.ts
│   │   ├── dict.ts
│   │   └── index.ts
│   │
│   ├── styles/                         # 样式体系
│   │   ├── variables.scss              #   设计令牌（颜色/间距/圆角/阴影/字号）
│   │   ├── reset.scss                  #   重置
│   │   ├── layout.scss                 #   布局
│   │   ├── transitions.scss            #   过渡动效
│   │   ├── element-override.scss       #   Element Plus 定制覆写
│   │   └── index.scss                  #   入口
│   │
│   ├── views/                          # 页面
│   │   ├── login/
│   │   │   └── index.vue
│   │   ├── dashboard/
│   │   │   └── index.vue
│   │   ├── system/
│   │   │   ├── user/
│   │   │   │   ├── index.vue
│   │   │   │   ├── edit.vue
│   │   │   │   └── detail.vue
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
│   │   │   └── index.vue
│   │   └── message/
│   │       └── index.vue
│   │
│   ├── App.vue
│   └── main.ts
│
├── public/
│   └── favicon.svg
├── docs/
│   └── frontend-design.md
├── .env
├── .env.production
├── index.html
├── package.json
├── vite.config.ts
├── tsconfig.json
├── ASSETS.md                           # 本文件
└── README.md
```

---

## 设计体系

### 配色方案

| 色板 | 用途 | 值 |
|------|------|-----|
| Primary | 主色调、按钮、链接、选中态 | `#2563EB`（蓝） |
| Primary Light | 悬浮、浅色背景 | `#3B82F6` |
| Primary Lighter | 标签背景、选中行 | `#EFF6FF` |
| Success | 成功状态、上线、通过 | `#16A34A` |
| Warning | 警告、待审核 | `#F59E0B` |
| Danger | 错误、删除、禁用 | `#EF4444` |
| Info | 信息、中性标签 | `#6B7280` |
| Background | 页面底色 | `#F8FAFC` |
| Surface | 卡片/面板底色 | `#FFFFFF` |
| Border | 边框 | `#E5E7EB` |
| Text Primary | 主文字 | `#111827` |
| Text Secondary | 次要文字 | `#6B7280` |
| Text Placeholder | 占位符 | `#9CA3AF` |

### 间距系统

| 级别 | 值 | 用途 |
|------|-----|------|
| xs | 4px | 图标与文字间距 |
| sm | 8px | 紧凑元素间距 |
| md | 12px | 表单项间距 |
| base | 16px | 卡片内边距 |
| lg | 20px | 区块间距 |
| xl | 24px | 页面内边距 |
| 2xl | 32px | 大区块间距 |

### 圆角

| 级别 | 值 | 用途 |
|------|-----|------|
| sm | 4px | 按钮、输入框 |
| base | 6px | 卡片、弹窗 |
| lg | 8px | 大卡片 |
| xl | 12px | 面板 |
| full | 9999px | 头像、标签 |

### 阴影

| 级别 | 值 | 用途 |
|------|-----|------|
| sm | `0 1px 2px rgba(0,0,0,0.05)` | 卡片 |
| base | `0 1px 3px rgba(0,0,0,0.1), 0 1px 2px rgba(0,0,0,0.06)` | 悬浮 |
| md | `0 4px 6px rgba(0,0,0,0.07), 0 2px 4px rgba(0,0,0,0.06)` | 弹窗 |
| lg | `0 10px 15px rgba(0,0,0,0.1), 0 4px 6px rgba(0,0,0,0.05)` | 下拉菜单 |

### 字号

| 级别 | 值 | 用途 |
|------|-----|------|
| xs | 12px | 辅助文字、标签 |
| sm | 13px | 表格内容 |
| base | 14px | 正文 |
| lg | 16px | 小标题 |
| xl | 18px | 页面标题 |
| 2xl | 24px | 大标题 |
| 3xl | 30px | Dashboard 数字 |

### 动效

| 场景 | 时长 | 曲线 | 说明 |
|------|------|------|------|
| 按钮悬浮 | 150ms | ease-out | 颜色/阴影过渡 |
| 弹窗打开 | 250ms | cubic-bezier(0.4, 0, 0.2, 1) | scale(0.95) → scale(1) + fade |
| 弹窗关闭 | 200ms | cubic-bezier(0.4, 0, 1, 1) | scale(1) → scale(0.95) + fade |
| 侧边栏展开 | 280ms | cubic-bezier(0.4, 0, 0.2, 1) | width 过渡 |
| 页面切换 | 200ms | ease-in-out | fade + translateX |
| 卡片悬浮 | 200ms | ease-out | translateY(-2px) + shadow 加深 |
| 加载骨架 | 1.5s | ease-in-out | 闪烁渐变 |
| 数字跳动 | 600ms | ease-out | Dashboard 数字计数动画 |

---

## 文件清单

### 基础设施

| 文件 | 说明 | 状态 |
|------|------|------|
| `vite.config.ts` | 构建配置（proxy + auto-import + alias） | ✅ |
| `tsconfig.app.json` | TypeScript 配置（@ 别名） | ✅ |
| `.env` / `.env.production` | 环境变量 | ✅ |
| `src/main.ts` | 应用入口 | ✅ |
| `src/App.vue` | 根组件 | ✅ |

### API 层

| 文件 | 说明 | 状态 |
|------|------|------|
| `src/api/request.ts` | Axios 封装（token 注入 / 401 refresh 重试 / loading / 错误提示） | ✅ |
| `src/api/crud.ts` | 通用 CRUD API 工厂（一个路径 → getList/getDetail/doEdit/doDelete） | ✅ |
| `src/api/types.ts` | ApiResponse / ListData / RequestOptions 类型 | ✅ |
| `src/api/modules/auth.ts` | 登录 / 登出 / 续期 / 用户信息 / 菜单权限 | ✅ |
| `src/api/modules/user.ts` | 用户 CRUD + 角色分配 | ✅ |
| `src/api/modules/role.ts` | 角色 CRUD + 菜单分配 | ✅ |
| `src/api/modules/menu.ts` | 菜单 CRUD + 菜单树 | ✅ |
| `src/api/modules/setting.ts` | 配置读写 | ✅ |
| `src/api/modules/message.ts` | 消息 CRUD + 未读数 + 标记已读 | ✅ |
| `src/api/modules/log.ts` | 操作日志 + 登录日志列表 | ✅ |

### 公共组件 & Hooks

| 文件 | 说明 | 状态 |
|------|------|------|
| `src/components/CrudTable/index.vue` | **核心** — 声明式 CRUD（搜索 + 表格 + 分页 + 弹窗 + 权限） | ✅ |
| `src/components/CrudTable/types.ts` | CrudColumn / SearchField / FormField 类型定义 | ✅ |
| `src/hooks/useCrud.ts` | CRUD 逻辑 Hook（列表 + 搜索 + 分页 + 增删改查） | ✅ |
| `src/hooks/usePermission.ts` | 权限判断 Hook | ✅ |

### 布局

| 文件 | 说明 | 状态 |
|------|------|------|
| `src/layouts/default/index.vue` | 主布局容器 | ✅ |
| `src/layouts/default/Sidebar.vue` | 侧边栏（深色 + 菜单树渲染） | ✅ |
| `src/layouts/default/Header.vue` | 顶栏（面包屑 + 消息铃铛 + 用户菜单） | ✅ |
| `src/layouts/default/TagsView.vue` | 标签栏（多页签 + 右键菜单） | ✅ |
| `src/layouts/default/AppMain.vue` | 内容区（keep-alive + 过渡动画） | ✅ |
| `src/layouts/blank/index.vue` | 空白布局（登录页） | ✅ |

### 路由 & 状态

| 文件 | 说明 | 状态 |
|------|------|------|
| `src/router/index.ts` | 路由实例 | ✅ |
| `src/router/static.ts` | 静态路由（登录 / 404） | ✅ |
| `src/router/dynamic.ts` | 后端菜单 → Vue 路由（template_path 映射） | ✅ |
| `src/router/guard.ts` | 路由守卫（鉴权 + 动态路由加载） | ✅ |
| `src/stores/user.ts` | 用户信息 + token + 权限列表 | ✅ |
| `src/stores/app.ts` | 应用设置（侧边栏折叠） | ✅ |
| `src/stores/permission.ts` | 菜单树 + 动态路由 | ✅ |
| `src/stores/tags.ts` | 标签栏状态（visited + cached） | ✅ |

### 样式

| 文件 | 说明 | 状态 |
|------|------|------|
| `src/styles/variables.scss` | 设计令牌（颜色 / 间距 / 圆角 / 字号 / 阴影） | ✅ |
| `src/styles/reset.scss` | 全局重置 + 滚动条 | ✅ |
| `src/styles/transitions.scss` | 页面切换 / 淡入淡出 / 侧边栏动效 | ✅ |
| `src/styles/element-override.scss` | Element Plus 覆写（扁平 + 4px 圆角 + 无阴影） | ✅ |
| `src/styles/index.scss` | 样式入口 | ✅ |

### 业务页面

| 文件 | 说明 | 模式 | 状态 |
|------|------|------|------|
| `src/views/login/index.vue` | 登录页（大色块品牌栏 + 表单） | 独立 | ✅ |
| `src/views/dashboard/index.vue` | 仪表盘（stat cards + 系统信息） | 独立 | ✅ |
| `src/views/system/user/index.vue` | 用户管理 | CrudTable 配置 | ✅ |
| `src/views/system/role/index.vue` | 角色管理 | CrudTable 配置 | ✅ |
| `src/views/system/menu/index.vue` | 菜单管理（树形表格） | 独立 | ✅ |
| `src/views/system/setting/index.vue` | 系统配置 | 独立 | ✅ |
| `src/views/system/log/operation/index.vue` | 操作日志 | CrudTable 配置（只读） | ✅ |
| `src/views/system/log/login/index.vue` | 登录日志 | CrudTable 配置（只读） | ✅ |
| `src/views/profile/index.vue` | 个人中心（资料 + 改密） | 独立 | ✅ |
| `src/views/message/index.vue` | 系统消息（标记已读 + 删除） | CrudTable + 自定义操作 | ✅ |

### 工具

| 文件 | 说明 | 状态 |
|------|------|------|
| `src/utils/auth.ts` | Token 存取（localStorage） | ✅ |

---

## 接口依赖

| 前端页面 | 后端接口 |
|---------|---------|
| 登录 | POST /api/admin/user/login |
| 续期 | POST /api/admin/user/refreshToken |
| 菜单+权限 | GET /api/admin/user/menus |
| 用户信息 | GET /api/admin/user/info |
| 用户 CRUD | /api/admin/user/getList, getDetail, doEdit, doDelete |
| 角色 CRUD | /api/admin/role/getList, getDetail, doEdit, doDelete |
| 角色菜单 | GET /api/admin/role/menuIds, POST /api/admin/role/assignMenus |
| 菜单 CRUD | /api/admin/menu/getList, getDetail, doEdit, doDelete |
| 菜单树 | GET /api/admin/menu/tree |
| 系统配置 | GET /api/admin/setting/get, POST /api/admin/setting/set |
| 操作日志 | GET /api/admin/operationLog/getList |
| 登录日志 | GET /api/admin/loginLog/getList |
| 消息 | /api/admin/message/getList, doDelete, markRead, unreadCount |
| 个人资料 | POST /api/admin/user/updateProfile |
| 修改密码 | POST /api/admin/user/changePassword |
