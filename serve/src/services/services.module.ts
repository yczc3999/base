import { Global, Module } from '@nestjs/common';
import { PrismaService } from './PrismaService.js';
import { RedisService } from './RedisService.js';

/**
 * 全局服务模块
 * PrismaService + RedisService 全局可注入
 */
@Global()
@Module({
  providers: [PrismaService, RedisService],
  exports: [PrismaService, RedisService],
})
export class ServicesModule {}
