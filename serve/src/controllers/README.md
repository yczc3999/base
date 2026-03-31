# controllers - 控制器层

## 职责

接收 HTTP 请求，调用 Logic 层处理业务，返回统一格式响应。

## 文件说明

| 文件 | 说明 |
|------|------|
| `BaseController.ts` | 基础控制器，提供 `success()` / `fail()` / `handleException()` 统一响应方法 |
| `CurdController.ts` | CRUD 控制器，继承 BaseController，注入 Logic 即拥有 `getList` / `getDetail` / `doEdit` / `doDelete` |
| `admin/` | 管理后台控制器目录，路由前缀 `/api/admin` |
| `client/` | 用户端控制器目录，路由前缀 `/api/client` |

## 对应 PHP 项目

等价于 PHP 项目中的 `app/controller/` 目录，将原来的 BaseController 和 CurdController 用 NestJS + TypeScript 重写。
