# Admin 前端 — 资产清单

> Vue 3 + TypeScript + Element Plus + Vite
> 设计体系：Geist 字体, Primary #2563EB, 4px 圆角上限, 扁平无阴影, Fluent/Material 风格

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
| 图表 | ECharts | 5.x |

---

## 目录结构

```
admin/
├── src/
│   ├── api/                            # API 层
│   │   ├── request.ts                  #   Axios 封装（NProgress + Promise-sharing refresh + MAX_RETRY=3）
│   │   ├── types.ts                    #   通用类型
│   │   ├── crud.ts                     #   createCrudApi(prefix) — 一行生成 CRUD 四接口
│   │   ├── settings.ts                 #   createSettingApi(category) — 配置读写工厂
│   │   └── modules/                    #   按模块
│   │       ├── auth.ts
│   │       ├── user.ts
│   │       ├── role.ts
│   │       ├── menu.ts
│   │       ├── setting.ts
│   │       ├── message.ts
│   │       ├── log.ts
│   │       ├── file.ts
│   │       └── export.ts               #   导出（进度轮询 + Blob 下载）
│   │
│   ├── components/                     # 公共组件
│   │   ├── CrudTable/                  #   声明式 CRUD 表格
│   │   │   ├── index.vue               #     搜索 + 表格 + 分页 + 弹窗 + 权限按钮
│   │   │   └── types.ts                #     CrudColumn / SearchField / FormField
│   │   ├── SettingForm/                #   多服务商配置（Tab 页 + single/parallel 模式）
│   │   │   └── index.vue
│   │   ├── ImageUpload/                #   图片上传（单/多，opens FileManager）
│   │   │   └── index.vue
│   │   ├── FileUpload/                 #   文件上传（拖拽 + 进度条）
│   │   │   └── index.vue
│   │   ├── FileManager/                #   文件管理器（网格 + 搜索 + 类型筛选 + 多选）
│   │   │   └── index.vue
│   │   └── IconPicker/                 #   图标选择器（8 分类 + 150+ 图标 + 1700 全局搜索）
│   │       └── index.vue
│   │
│   ├── layouts/                        # 布局
│   │   ├── default/                    #   后台主布局
│   │   │   ├── index.vue
│   │   │   ├── Sidebar.vue
│   │   │   ├── Header.vue
│   │   │   ├── TagsView.vue
│   │   │   └── AppMain.vue
│   │   └── blank/                      #   空白布局（登录页）
│   │       └── index.vue
│   │
│   ├── router/                         # 路由
│   │   ├── index.ts
│   │   ├── guard.ts                    #   鉴权守卫（MAX_RETRY=3）
│   │   ├── static.ts
│   │   └── dynamic.ts                  #   后端菜单 → Vue 路由（template_path 映射）
│   │
│   ├── stores/                         # Pinia 状态
│   │   ├── user.ts                     #   用户信息 + token
│   │   ├── app.ts                      #   应用设置（侧边栏折叠）
│   │   ├── permission.ts               #   菜单树 + 权限列表 + 动态路由
│   │   ├── tags.ts                     #   标签栏（visited + cached）
│   │   └── site.ts                     #   站点配置（从 settings API 加载，绑定登录页 + 侧边栏）
│   │
│   ├── utils/                          # 工具
│   │   └── auth.ts                     #   Token 存取（localStorage）
│   │
│   ├── styles/                         # 样式体系
│   │   ├── variables.scss              #   设计令牌
│   │   ├── reset.scss                  #   全局重置 + 滚动条
│   │   ├── layout.scss                 #   page-title（蓝色左竖条）+ content-card + dialog-footer
│   │   ├── transitions.scss            #   页面切换 / 淡入淡出
│   │   ├── element-override.scss       #   Element Plus 覆写（扁平 + 4px 圆角 + 无阴影）
│   │   └── index.scss                  #   样式入口
│   │
│   ├── views/                          # 页面
│   │   ├── login/index.vue             #   登录页（品牌栏 + 表单 + 站点配置绑定）
│   │   ├── dashboard/index.vue         #   仪表盘（stat cards + 系统信息 + 最近操作）
│   │   ├── system/
│   │   │   ├── user/                   #   用户管理（CrudTable + 角色分配）
│   │   │   ├── role/                   #   角色管理（CrudTable + 菜单权限分配）
│   │   │   ├── menu/                   #   菜单管理（树形表格 + IconPicker）
│   │   │   ├── setting/                #   系统配置
│   │   │   └── log/                    #   日志（操作日志 + 登录日志，CrudTable 只读 + 导出）
│   │   ├── settings/                   #   服务商配置
│   │   │   ├── sms/                    #     短信（SettingForm 多 Tab）
│   │   │   ├── storage/                #     存储
│   │   │   ├── notify/                 #     通知
│   │   │   ├── payment/                #     支付
│   │   │   └── site/                   #     站点配置
│   │   ├── profile/index.vue           #   个人中心（资料 + 改密）
│   │   └── message/index.vue           #   系统消息（标记已读 + 删除）
│   │
│   ├── App.vue
│   └── main.ts
│
├── .env / .env.production
├── index.html
├── package.json
├── vite.config.ts
├── tsconfig.json
├── ASSETS.md
└── README.md
```

---

## 设计体系

### 配色

| 色板 | 值 | 用途 |
|------|-----|------|
| Primary | `#2563EB` | 主色调、按钮、链接、选中态 |
| Primary Light | `#3B82F6` | 悬浮态 |
| Primary Lighter | `#EFF6FF` | 标签背景、选中行 |
| Success | `#16A34A` | 成功 |
| Warning | `#F59E0B` | 警告 |
| Danger | `#EF4444` | 错误、删除 |
| Info | `#6B7280` | 中性 |
| Background | `#F8FAFC` | 页面底色 |
| Border | `#E5E7EB` | 边框 |
| Text Primary | `#111827` | 主文字 |
| Text Secondary | `#6B7280` | 次要文字 |

### 关键规则

- **圆角上限 4px** — 按钮、输入框、卡片、弹窗统一 4px
- **扁平无阴影** — 用边框区分层级，不用 box-shadow
- **所有按钮带图标** — Lucide icons，统一尺寸
- **字体 Geist** — 等宽数字 + 清晰西文 + 系统中文 fallback

---

## 核心组件

| 组件 | 功能 |
|------|------|
| **CrudTable** | 声明式 CRUD：columns + formFields → 搜索 + 表格 + 分页 + 弹窗 + 权限 + 导出（`exportable` prop） |
| **SettingForm** | 多服务商配置：Tab 页切换驱动，single（互斥）/parallel（并行）两种模式 |
| **ImageUpload** | 图片上传：单/多模式，点击"从文件库选择"打开 FileManager |
| **FileUpload** | 文件上传：拖拽区 + 上传进度条 + 文件列表 |
| **FileManager** | 文件管理器：网格视图 + 搜索 + 类型筛选 + 多选（带数量限制） |
| **IconPicker** | 图标选择器：8 个分类 150+ 常用图标 + 1700 全局搜索 |

## API 工厂

| 工厂 | 用法 |
|------|------|
| `createCrudApi(prefix)` | 一个路径 → getList/getDetail/doEdit/doDelete/doExport |
| `createSettingApi(category)` | 一个 category → get/set 配置读写 |

## Hooks

| Hook | 功能 |
|------|------|
| `useCrud` | CRUD 逻辑封装（列表 + 搜索 + 分页 + 增删改查） |
| `usePermission` | 权限判断（hasPerms） |
| `useExport` | 导出流程（触发 → 轮询进度 → 自动 Blob 下载） |

---

## 接口依赖

| 前端页面 | 后端接口 |
|---------|---------|
| 登录 | POST /api/admin/user/login |
| 续期 | POST /api/admin/user/refreshToken |
| 菜单+权限 | GET /api/admin/user/menus |
| 用户信息 | GET /api/admin/user/info |
| 用户 CRUD | /api/admin/user/getList, getDetail, doEdit, doDelete |
| 角色 CRUD | /api/admin/role/* |
| 角色菜单 | GET /api/admin/role/menuIds, POST /api/admin/role/assignMenus |
| 菜单 CRUD | /api/admin/menu/* + GET tree |
| 系统配置 | GET /api/admin/setting/get, POST /api/admin/setting/set |
| 服务商配置 | GET /api/admin/setting/get?category=xxx, POST set |
| 操作日志 | GET /api/admin/operationLog/getList |
| 登录日志 | GET /api/admin/loginLog/getList |
| 消息 | /api/admin/message/* + markRead + unreadCount |
| 文件上传 | POST /api/admin/file/upload, uploadImage, batchDelete |
| 文件管理 | /api/admin/file/getList |
| 仪表盘 | GET /api/admin/dashboard/stats, system, recent |
| 个人资料 | POST /api/admin/user/updateProfile |
| 修改密码 | POST /api/admin/user/changePassword |
