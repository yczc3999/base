# filters - 异常过滤器层

## 职责

捕获未被 Controller 处理的异常，统一转换为标准响应格式，避免直接暴露错误堆栈。

## 文件说明

| 文件 | 说明 |
|------|------|
| `GlobalExceptionFilter.ts` | 全局异常过滤器，HTTP 层始终返回 200 状态码，业务错误通过 `code` 字段区分；调试模式下返回错误详情 |

## 对应 PHP 项目

等价于 PHP 项目中的全局异常处理器（如 `app/exception/Handler.php`），在 NestJS 中使用 ExceptionFilter 机制实现。
