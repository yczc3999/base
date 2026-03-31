# routes - 路由分组

## 职责

通过 NestJS RouterModule 实现路由分组，将不同业务的控制器注册到对应的 URL 前缀下。

## 文件说明

| 文件 | 说明 |
|------|------|
| `admin.ts` | 管理后台路由模块，前缀 `/api/admin`，注册管理端控制器 |
| `client.ts` | 用户端路由模块，前缀 `/api/client`，注册用户端控制器 |

## 对应 PHP 项目

等价于 PHP 项目中的 `config/route.php` 路由配置文件，通过分组实现 admin / client 路由隔离。
