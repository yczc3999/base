import { Injectable } from '@nestjs/common';
import { PrismaService } from '../services/PrismaService.js';
import { RedisService } from '../services/RedisService.js';
import { BaseLogic } from './BaseLogic.js';

/**
 * 管理员操作日志逻辑层
 *
 * 记录管理员的所有操作行为（增删改查），用于审计追踪。
 * 不开启缓存 — 日志是写多读少的场景。
 */
@Injectable()
export class AdminOperationLogLogic extends BaseLogic {
  constructor(
    private readonly prisma: PrismaService,
    redis: RedisService,
  ) {
    super(prisma.adminOperationLog, redis);
  }

  /**
   * 记录操作日志
   *
   * @param data - 日志数据
   * @param data.userId - 操作人 ID
   * @param data.username - 操作人用户名
   * @param data.module - 操作模块（Controller 名称）
   * @param data.action - 操作方法名
   * @param data.method - HTTP 方法
   * @param data.url - 请求 URL
   * @param data.params - 请求参数（JSON 字符串）
   * @param data.ip - 请求 IP
   * @param data.userAgent - User-Agent
   * @param data.statusCode - 响应业务码
   * @param data.duration - 耗时（ms）
   */
  async record(data: {
    userId: number;
    username: string;
    module: string;
    action: string;
    method: string;
    url: string;
    params?: string;
    ip?: string;
    userAgent?: string;
    statusCode?: number;
    duration?: number;
  }): Promise<void> {
    // 异步写入，不阻塞主流程
    this.prisma.adminOperationLog.create({ data }).catch(() => {
      // 日志写入失败不应影响业务
    });
  }
}
