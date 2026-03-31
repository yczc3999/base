# logics - 逻辑层

## 职责

承载核心业务逻辑，包括 CRUD 操作、缓存管理、数据格式化。位于 Controller 和 Model（Prisma）之间。

## 文件说明

| 文件 | 说明 |
|------|------|
| `BaseLogic.ts` | 基础逻辑类，提供完整的 CRUD + 多字段缓存 + 数据格式化 + 生命周期钩子 |

## 对应 PHP 项目

等价于 PHP 项目中的 `app/logic/` 目录，对应 BaseLogic 基类。子类只需传入 Prisma modelDelegate 即可继承全部能力。
