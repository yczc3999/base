# Base Admin — 前端

基于 **Vue 3 + TypeScript + Element Plus + Vite** 的通用后台管理系统前端。

> 本目录只维护通用基础能力。任何具体项目必须先 FORK/CLONE 本仓库，再在自己的仓库中开发；禁止把具体产品页面、品牌和业务 API 直接写入 Base。

## 核心设计

**声明式 CRUD —— 一个配置生成一个完整页面。**

```vue
<template>
  <CrudTable api="admin/user" perms="admin:user" :columns="columns" :form-fields="formFields" />
</template>
```

一个 `api` 字符串自动生成 `getList` / `getDetail` / `doEdit` / `doDelete` 四个接口调用。配合 `columns` / `formFields` 配置，自动渲染搜索栏、表格、分页、弹窗表单、权限按钮。

**新增一个 CRUD 页面 = 复制一个 `.vue` 文件 → 改 `api` 路径 + 列配置 → 完事。**

## 技术栈

| 项 | 选型 |
|----|------|
| 框架 | Vue 3 (Composition API + `<script setup>`) |
| 语言 | TypeScript |
| 构建 | Vite |
| UI | Element Plus（覆写为扁平锐角风格，4px 圆角上限，无阴影） |
| 状态 | Pinia |
| 路由 | Vue Router 4（动态路由，后端菜单驱动） |
| HTTP | Axios（NProgress + Promise-sharing refresh + MAX_RETRY=3） |
| 图标 | Lucide Vue Next |
| 图表 | ECharts |

## 核心组件

| 组件 | 功能 |
|------|------|
| **CrudTable** | 声明式 CRUD：columns + formFields → 搜索 + 表格 + 分页 + 弹窗 + 权限 + 导出 |
| **SettingForm** | 多服务商配置：Tab 页切换，single（互斥）/ parallel（并行）两种模式 |
| **ImageUpload** | 图片上传：单/多模式，可打开 FileManager 选择 |
| **FileUpload** | 文件上传：拖拽区 + 进度条 + 文件列表 |
| **FileManager** | 文件管理器：网格 + 搜索 + 类型筛选 + 多选（带数量限制） |
| **IconPicker** | 图标选择器：8 个分类 150+ 图标 + 1700 全局搜索 |

## API 工厂

```ts
// 一行 CRUD 接口
const api = createCrudApi('admin/order')
// → api.getList, api.getDetail, api.doEdit, api.doDelete, api.doExport

// 一行配置读写
const settingApi = createSettingApi('sms')
// → settingApi.get(), settingApi.set(data)
```

## 项目结构

```
src/
├── api/                    # API 层
│   ├── request.ts          #   Axios 封装（token / refresh / NProgress / 错误提示）
│   ├── crud.ts             #   createCrudApi() — CRUD 工厂
│   ├── settings.ts         #   createSettingApi() — 配置工厂
│   ├── types.ts            #   通用类型
│   └── modules/            #   业务 API（auth/user/role/menu/setting/message/log/file）
│
├── components/             # 公共组件（CrudTable/SettingForm/ImageUpload/FileUpload/FileManager/IconPicker）
├── layouts/                # 布局（default: Sidebar+Header+TagsView+AppMain / blank: 登录页）
├── router/                 # 路由（static + dynamic + guard）
├── stores/                 # Pinia（user / app / permission / tags / site）
├── styles/                 # 设计令牌 + Element Plus 覆写 + layout 工具类
├── utils/                  # Token 管理
└── views/                  # 页面
    ├── login/              #   登录（品牌栏 + 站点配置绑定）
    ├── dashboard/          #   仪表盘（统计卡片 + 系统信息 + 最近操作）
    ├── system/             #   用户 / 角色 / 菜单 / 配置 / 日志
    ├── settings/           #   服务商配置（短信 / 存储 / 通知 / 支付 / 站点）
    ├── profile/            #   个人中心
    └── message/            #   系统消息
```

## 快速开始

```bash
npm install
npm run dev
```

下游首次 Fork/Clone 推荐从仓库根目录运行
`scripts/bootstrap-project.sh PROJECT_SLUG "Project Name"`；它使用 `npm ci` 安装
依赖并生成 Git 忽略的 `admin/.env`，其中 `VITE_APP_TITLE` 为项目名称。

## 新增 CRUD 页面

**后端有 `crud_router("order", order_logic)` → 前端新建一个 `.vue`：**

```vue
<template>
  <CrudTable api="admin/order" perms="admin:order" :columns="columns" :form-fields="formFields" />
</template>

<script setup lang="ts">
import CrudTable from '@/components/CrudTable/index.vue'
import type { CrudColumn, FormField } from '@/components/CrudTable/types'

const columns: CrudColumn[] = [
  { field: 'id', label: 'ID', width: 70 },
  { field: 'order_no', label: '订单号' },
  { field: 'status', label: '状态', type: 'status', statusMap: {
    0: { label: '待付款', type: 'warning' },
    1: { label: '已付款', type: 'success' },
  }},
  { field: 'created_at', label: '创建时间', type: 'time' },
]

const formFields: FormField[] = [
  { field: 'order_no', label: '订单号', rules: [{ required: true }] },
  { field: 'amount', label: '金额', type: 'number' },
]
</script>
```

**后端菜单表加一条 `template_path = "xxx/order/index"` → 路由自动生成。完事。**

## 新增服务商配置页

```vue
<template>
  <SettingForm category="payment" mode="single" :providers="providers" />
</template>
```

SettingForm 自动处理 Tab 切换、配置加载/保存、多服务商选择。`mode="single"` 表示同一时间只启用一个服务商，`"parallel"` 表示可同时启用多个。

## 启用数据导出

CrudTable 加一个 `exportable` 即可：

```vue
<CrudTable api="admin/operationLog" :columns="columns" exportable />
```

前提：后端 Logic 覆写了 `export_header_map()`。

流程自动化：点击导出按钮 → 推队列异步生成 XLSX → 进度条实时更新 → 完成后自动下载。
