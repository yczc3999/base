# Base Serve - 后台服务端

基于 NestJS 重写的后台管理系统服务端，从 PHP 项目迁移而来。提供完整的 CRUD 基础架构、JWT 认证、RBAC 权限体系、Redis 缓存等能力。

## 技术栈

- **运行时**: Node.js + TypeScript
- **框架**: NestJS 11
- **ORM**: Prisma 7（PostgreSQL 适配器）
- **缓存**: Redis（ioredis）
- **认证**: JWT（jsonwebtoken）
- **验证**: class-validator + class-transformer

## 目录结构

```
src/
├── controllers/          # 控制器层
│   ├── BaseController.ts   # 基础控制器（统一响应格式）
│   ├── CurdController.ts   # CRUD 控制器（注入 Logic 即拥有增删改查）
│   ├── admin/              # 管理后台控制器
│   └── client/             # 用户端控制器
├── logics/               # 逻辑层
│   └── BaseLogic.ts        # 基础逻辑类（CRUD + 缓存 + 格式化 + 生命周期钩子）
├── models/               # 模型扩展 & 类型定义
├── routes/               # 路由分组
│   ├── admin.ts            # 管理后台路由（/api/admin）
│   └── client.ts           # 用户端路由（/api/client）
├── guards/               # 守卫
│   └── AuthGuard.ts        # JWT 认证守卫
├── decorators/           # 自定义装饰器
│   ├── Public.ts           # @Public() 免认证标记
│   └── Actions.ts          # @Actions() 操作类型映射
├── interceptors/         # 拦截器
│   └── ResponseInterceptor.ts  # 统一响应格式包装
├── filters/              # 异常过滤器
│   └── GlobalExceptionFilter.ts  # 全局异常处理
├── services/             # 基础服务
│   ├── PrismaService.ts    # 数据库服务
│   ├── RedisService.ts     # Redis 缓存服务
│   └── services.module.ts  # 服务模块注册
├── utils/                # 工具类
│   └── Token.ts            # JWT 签发 & 验证
├── app.module.ts         # 根模块
└── main.ts               # 入口文件
```

## 开发 & 运行

```bash
# 安装依赖
npm install

# 开发模式（热重载）
npm run start:dev

# 生产构建
npm run build
npm run start:prod
```

## TODO 进度

### 基础架构

- [x] 项目初始化（NestJS + TypeScript + Prisma 7）
- [x] 目录结构搭建
- [x] BaseController + CurdController
- [x] BaseLogic（CRUD + 缓存 + 格式化）
- [x] RedisService + PrismaService
- [x] AuthGuard + @Public + @Actions 装饰器
- [x] ResponseInterceptor + GlobalExceptionFilter
- [x] 路由分组（admin / client）

### 核心模块

- [ ] SettingLogic（系统配置 + 全量缓存）
- [ ] User 模块（登录/注册/用户管理）
- [ ] RBAC 权限体系（Menu / Role / Permission）
- [ ] PermissionGuard（操作级权限校验）

### 业务功能

- [ ] 数据导出
- [ ] 操作日志
- [ ] 文件管理

### 前端后台

- [ ] Admin 前端搭建
