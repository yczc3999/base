# guards - 守卫层

## 职责

实现请求级别的访问控制，在请求到达 Controller 之前进行认证和权限校验。

## 文件说明

| 文件 | 说明 |
|------|------|
| `AuthGuard.ts` | JWT 认证守卫，全局注册，验证 token 有效性和 token_version，`@Public()` 标记的路由跳过认证 |

## 对应 PHP 项目

等价于 PHP 项目中的中间件认证逻辑（如 `app/middleware/AuthMiddleware.php`），在 NestJS 中使用 Guard 机制实现。
