import { Injectable, OnModuleInit, OnModuleDestroy } from '@nestjs/common';
import { PrismaPg } from '@prisma/adapter-pg';
import { PrismaClient } from '../models/generated/client.js';

/**
 * 从环境变量构建 PostgreSQL 连接 URL
 *
 * 读取 DATABASE_HOST / DATABASE_PORT / DATABASE_NAME / DATABASE_USER /
 * DATABASE_PASSWORD / DATABASE_SCHEMA 环境变量，拼接为标准 PostgreSQL 连接字符串。
 *
 * @returns PostgreSQL 连接 URL 字符串
 */
function buildDatabaseUrl(): string {
  const { DATABASE_HOST, DATABASE_PORT, DATABASE_NAME, DATABASE_USER, DATABASE_PASSWORD, DATABASE_SCHEMA } = process.env;
  return `postgresql://${DATABASE_USER}:${DATABASE_PASSWORD}@${DATABASE_HOST}:${DATABASE_PORT}/${DATABASE_NAME}?schema=${DATABASE_SCHEMA}`;
}

/**
 * Prisma 数据库服务
 *
 * 继承 PrismaClient，作为 NestJS 全局可注入的服务。
 * 使用 PrismaPg 适配器连接 PostgreSQL，自动管理连接生命周期：
 * - 模块初始化时建立连接（onModuleInit）
 * - 模块销毁时断开连接（onModuleDestroy）
 */
@Injectable()
export class PrismaService extends PrismaClient implements OnModuleInit, OnModuleDestroy {
  /**
   * 构造 PrismaService 实例
   *
   * 通过 PrismaPg 适配器建立与 PostgreSQL 的连接，
   * 连接参数从环境变量中读取。
   */
  constructor() {
    const adapter = new PrismaPg({ connectionString: buildDatabaseUrl() });
    super({ adapter });
  }

  /**
   * 模块初始化时自动连接数据库
   */
  async onModuleInit() {
    await this.$connect();
  }

  /**
   * 模块销毁时自动断开数据库连接
   */
  async onModuleDestroy() {
    await this.$disconnect();
  }
}
