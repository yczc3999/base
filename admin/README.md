# Base Admin — 前端

基于 **Vue 3 + TypeScript + Element Plus + Vite** 的通用后台管理系统前端。

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
| UI | Element Plus（覆写为扁平锐角风格） |
| 状态 | Pinia |
| 路由 | Vue Router 4（动态路由，后端菜单驱动） |
| HTTP | Axios（封装 token 注入 / refresh 重试 / loading / 错误提示） |

## 项目结构

```
src/
├── api/                    # API 层
│   ├── request.ts          #   Axios 封装（token / refresh / loading）
│   ├── crud.ts             #   通用 CRUD API 工厂（一个路径 → 四个接口）
│   ├── types.ts            #   通用类型
│   └── modules/            #   业务 API（auth/user/role/menu/setting/message/log）
│
├── components/             # 公共组件
│   └── CrudTable/          #   声明式 CRUD 组件
│       ├── index.vue       #     搜索 + 表格 + 分页 + 弹窗表单 + 权限按钮
│       └── types.ts        #     CrudColumn / SearchField / FormField 类型
│
├── hooks/                  # 组合式函数
│   ├── useCrud.ts          #   CRUD 逻辑封装（列表 + 搜索 + 增删改查）
│   └── usePermission.ts    #   权限判断
│
├── layouts/                # 布局
│   ├── default/            #   Sidebar + Header + TagsView + AppMain
│   └── blank/              #   空白布局（登录页）
│
├── router/                 # 路由
│   ├── static.ts           #   静态路由（登录 / 404）
│   ├── dynamic.ts          #   后端菜单 → Vue 路由
│   └── guard.ts            #   鉴权守卫
│
├── stores/                 # Pinia（user / app / permission / tags）
├── styles/                 # 设计令牌 + Element Plus 覆写
├── utils/                  # Token 管理
└── views/                  # 页面（每个 CRUD 页面只有配置）
```

## 快速开始

```bash
npm install
npm run dev
```

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
