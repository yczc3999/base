# models - 模型层

## 职责

存放 Prisma 模型相关的扩展定义、DTO、类型声明等。实际的数据库模型定义在 `prisma/schema.prisma` 中，由 Prisma 自动生成。

## 对应 PHP 项目

等价于 PHP 项目中的 `app/model/` 目录。在 NestJS 项目中，ORM 模型由 Prisma schema 自动生成，此目录用于放置额外的模型扩展和类型定义。
