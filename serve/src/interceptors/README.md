# interceptors - 拦截器层

## 职责

在请求处理前后对数据进行统一加工，主要用于响应格式统一包装。

## 文件说明

| 文件 | 说明 |
|------|------|
| `ResponseInterceptor.ts` | 统一响应拦截器，将所有响应包装为 `{ code, msg, data }` 格式；已通过 BaseController 格式化的数据直接透传 |

## 对应 PHP 项目

等价于 PHP 项目中的响应中间件，在 NestJS 中使用 Interceptor 机制实现统一响应格式。
