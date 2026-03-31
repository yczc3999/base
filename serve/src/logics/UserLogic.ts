import { Injectable } from '@nestjs/common';
import { PrismaService } from '../services/PrismaService.js';
import { RedisService } from '../services/RedisService.js';
import { BaseLogic } from './BaseLogic.js';
import { TokenUtil } from '../utils/Token.js';
import * as bcrypt from 'bcryptjs';

/**
 * 前端用户逻辑层
 *
 * 对应 users 表，提供前端用户的 CRUD + 登录注册。
 */
@Injectable()
export class UserLogic extends BaseLogic {
  protected cachePrefix = 'User';
  protected needCache = true;
  protected exceptKeys = ['password'];

  constructor(
    private readonly prisma: PrismaService,
    private readonly redisService: RedisService,
  ) {
    super(prisma.user, redisService);
  }

  /**
   * 用户登录
   *
   * @param username - 用户名
   * @param password - 明文密码
   * @returns 包含 token 和用户信息的对象
   * @throws Error 用户名不存在、密码错误、账号被禁用时抛出
   */
  async login(username: string, password: string): Promise<{ token: string; user: Record<string, any> }> {
    const user = await this.getDetail({ username }, { withSensitive: true });
    if (!user) throw new Error('用户名不存在');
    if (user.status !== 1) throw new Error('账号已被禁用');

    const valid = await bcrypt.compare(password, user.password);
    if (!valid) throw new Error('密码错误');

    const ssoEnabled = process.env.SSO_ENABLED === 'true';
    let tokenVersion = user.tokenVersion;
    if (ssoEnabled) {
      tokenVersion += 1;
      await this.prisma.user.update({
        where: { id: user.id },
        data: { tokenVersion },
      });
      const appName = process.env.APP_NAME || 'app';
      await this.redisService.set(`${appName}:TOKEN_VER:client:${user.id}`, String(tokenVersion), 86400);
    }

    await this.prisma.user.update({
      where: { id: user.id },
      data: { lastLoginAt: new Date() },
    });

    const token = TokenUtil.sign({
      userId: user.id,
      tokenVersion,
      scope: 'client',
    });

    const userInfo = await this.getDetail(user.id);
    return { token, user: userInfo! };
  }

  /**
   * 用户注册
   *
   * @param data - 注册数据，必须包含 username 和 password
   * @returns 注册成功后的用户信息（不含密码）
   * @throws Error 用户名已存在时抛出
   */
  async register(data: { username: string; password: string; nickname?: string }): Promise<Record<string, any>> {
    const exists = await this.getDetail({ username: data.username });
    if (exists) throw new Error('用户名已存在');
    return this.doCreate(data);
  }

  /**
   * 创建前钩子 — 密码加密
   */
  protected beforeCreate(data: Record<string, any>): Record<string, any> {
    if (data.password) {
      data.password = bcrypt.hashSync(data.password, 10);
    }
    return data;
  }

  /**
   * 编辑前钩子 — 密码处理
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
