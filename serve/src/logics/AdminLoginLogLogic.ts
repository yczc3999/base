import { Injectable } from '@nestjs/common';
import { PrismaService } from '../services/PrismaService.js';
import { RedisService } from '../services/RedisService.js';
import { BaseLogic } from './BaseLogic.js';

/**
 * 管理员登录日志逻辑层
 *
 * 记录管理员的登录行为（成功/失败），用于安全审计。
 */
@Injectable()
export class AdminLoginLogLogic extends BaseLogic {
  constructor(
    private readonly prisma: PrismaService,
    redis: RedisService,
  ) {
    super(prisma.adminLoginLog, redis);
  }

  /**
   * 记录登录日志
   *
   * @param data - 日志数据
   * @param data.userId - 用户 ID（登录失败时可为 0）
   * @param data.username - 用户名
   * @param data.ip - 登录 IP
   * @param data.userAgent - User-Agent
   * @param data.status - 1=成功 0=失败
   * @param data.remark - 备注（如失败原因）
   */
  async record(data: {
    userId: number;
    username: string;
    ip?: string;
    userAgent?: string;
    status: number;
    remark?: string;
  }): Promise<void> {
    this.prisma.adminLoginLog.create({ data }).catch(() => {});
  }
}
