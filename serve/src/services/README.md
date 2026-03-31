# services - 基础服务层

## 职责

封装底层基础设施服务（数据库、缓存等），作为全局单例供各模块注入使用。

## 文件说明

| 文件 | 说明 |
|------|------|
| `PrismaService.ts` | Prisma 数据库服务，继承 PrismaClient，管理数据库连接生命周期 |
| `RedisService.ts` | Redis 缓存服务，封装 ioredis，提供 get/set/del/scan 等常用操作 |
| `services.module.ts` | 服务模块，将 PrismaService 和 RedisService 注册为全局可注入的 Provider |

## 对应 PHP 项目

等价于 PHP 项目中的数据库连接和 Redis 连接配置，在 NestJS 中通过依赖注入管理服务实例。
