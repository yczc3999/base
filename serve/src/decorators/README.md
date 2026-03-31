# decorators - 自定义装饰器

## 职责

提供自定义装饰器，用于在 Controller 或方法上声明元数据，配合 Guard / Interceptor 实现声明式的认证和权限控制。

## 文件说明

| 文件 | 说明 |
|------|------|
| `Public.ts` | `@Public()` 装饰器，标记无需认证的路由 |
| `Actions.ts` | `@Actions()` 装饰器，声明 Controller 方法的操作类型映射（read / write / delete），供权限守卫使用 |

## 对应 PHP 项目

等价于 PHP 项目中通过注解或配置实现的权限标记机制。NestJS 使用 TypeScript 装饰器 + Reflector 实现同样的功能。
