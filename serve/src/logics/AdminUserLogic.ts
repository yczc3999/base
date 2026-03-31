import { Injectable } from '@nestjs/common';
import { PrismaService } from '../services/PrismaService.js';
import { RedisService } from '../services/RedisService.js';
import { BaseLogic } from './BaseLogic.js';
import { AdminLoginLogLogic } from './AdminLoginLogLogic.js';
import { TokenUtil } from '../utils/Token.js';
import * as bcrypt from 'bcryptjs';

/**
 * 管理员用户逻辑层
 *
 * 对应 admin_users 表，提供管理员的 CRUD + 登录认证。
 * exceptKeys 过滤 password 字段，登录验证时用 withSensitive 获取。
 */
@Injectable()
export class AdminUserLogic extends BaseLogic {
  /** 缓存前缀 */
  protected cachePrefix = 'AdminUser';

  /** 开启缓存 */
  protected needCache = true;

  /** 过滤密码字段 */
  protected exceptKeys = ['password'];

  constructor(
    private readonly prisma: PrismaService,
    private readonly redisService: RedisService,
    private readonly loginLogLogic: AdminLoginLogLogic,
  ) {
    super(prisma.adminUser, redisService);
  }

  /**
   * 管理员登录
   *
   * @param username - 用户名
   * @param password - 明文密码
   * @returns 包含 token 和用户信息的对象
   * @throws Error 用户名不存在、密码错误、账号被禁用时抛出
   */
  async login(username: string, password: string, ip?: string, userAgent?: string): Promise<{ token: string; user: Record<string, any> }> {
    const user = await this.getDetail({ username }, { withSensitive: true });
    if (!user) {
      this.loginLogLogic.record({ userId: 0, username, ip, userAgent, status: 0, remark: '用户名不存在' });
      throw new Error('用户名不存在');
    }
    if (user.status !== 1) {
      this.loginLogLogic.record({ userId: user.id, username, ip, userAgent, status: 0, remark: '账号已被禁用' });
      throw new Error('账号已被禁用');
    }

    const valid = await bcrypt.compare(password, user.password);
    if (!valid) {
      this.loginLogLogic.record({ userId: user.id, username, ip, userAgent, status: 0, remark: '密码错误' });
      throw new Error('密码错误');
    }

    // SSO 模式：递增 token_version，使旧 token 失效
    const ssoEnabled = process.env.SSO_ENABLED === 'true';
    let tokenVersion = user.tokenVersion;
    if (ssoEnabled) {
      tokenVersion += 1;
      await this.prisma.adminUser.update({
        where: { id: user.id },
        data: { tokenVersion },
      });
      // 刷新 Redis 中的 token_version 缓存
      const appName = process.env.APP_NAME || 'app';
      await this.redisService.set(`${appName}:TOKEN_VER:admin:${user.id}`, String(tokenVersion), 86400);
    }

    // 更新最后登录时间和 IP
    await this.prisma.adminUser.update({
      where: { id: user.id },
      data: { lastLoginAt: new Date() },
    });

    const token = TokenUtil.sign({
      userId: user.id,
      tokenVersion,
      scope: 'admin',
    });

    // 记录登录成功日志
    this.loginLogLogic.record({ userId: user.id, username, ip, userAgent, status: 1 });

    // 返回不含密码的用户信息
    const userInfo = await this.getDetail(user.id);
    return { token, user: userInfo! };
  }

  /**
   * 修改密码
   *
   * @param userId - 用户 ID
   * @param oldPassword - 旧密码
   * @param newPassword - 新密码
   * @throws Error 旧密码错误时抛出
   */
  async changePassword(userId: number, oldPassword: string, newPassword: string): Promise<void> {
    const user = await this.getDetail(userId, { withSensitive: true });
    if (!user) throw new Error('用户不存在');

    const valid = await bcrypt.compare(oldPassword, user.password);
    if (!valid) throw new Error('原密码错误');

    const hashed = await bcrypt.hash(newPassword, 10);
    const newVersion = user.tokenVersion + 1;

    await this.prisma.adminUser.update({
      where: { id: userId },
      data: { password: hashed, tokenVersion: newVersion },
    });

    // 刷新 Redis token_version，使旧 token 失效
    const appName = process.env.APP_NAME || 'app';
    await this.redisService.set(`${appName}:TOKEN_VER:admin:${userId}`, String(newVersion), 86400);

    // 清除用户缓存
    if (user) await this.clearCache(user);
  }

  /**
   * 创建前钩子 — 密码加密
   *
   * @param data - 待创建数据
   * @returns 密码加密后的数据
   */
  protected beforeCreate(data: Record<string, any>): Record<string, any> {
    if (data.password) {
      data.password = bcrypt.hashSync(data.password, 10);
    }
    return data;
  }

  /**
   * 编辑前钩子 — 如果传了密码则加密，没传则移除
   *
   * @param data - 待更新数据
   * @returns 处理后的数据
   */
  protected beforeEdit(data: Record<string, any>): Record<string, any> {
    if (data.password) {
      data.password = bcrypt.hashSync(data.password, 10);
    } else {
      delete data.password;
    }
    return data;
  }
}
