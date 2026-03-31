import { Injectable, CanActivate, ExecutionContext, UnauthorizedException } from '@nestjs/common';
import { Reflector } from '@nestjs/core';
import { IS_PUBLIC_KEY } from '../decorators/Public.js';
import { TokenUtil } from '../utils/Token.js';
import { RedisService } from '../services/RedisService.js';
import { PrismaService } from '../services/PrismaService.js';

/**
 * JWT 认证守卫
 *
 * 全局注册，在每个请求到达 Controller 之前执行认证校验。
 * 使用 @Public() 装饰器标记的路由会跳过认证。
 *
 * 认证流程：
 * 1. 检查路由是否标记为 @Public()，是则直接放行
 * 2. 从 Authorization 请求头提取 Bearer token
 * 3. 验证 JWT token 签名和有效期
 * 4. 根据 token 中的 scope（admin / client）查询对应的用户表
 * 5. 校验 token_version（先查 Redis 缓存，未命中则查数据库并回写缓存）
 * 6. 将用户信息（payload）挂载到 request.user 供后续使用
 *
 * token_version 机制：用户修改密码或主动登出时递增 token_version，
 * 使之前签发的所有 token 立即失效。
 */
@Injectable()
export class AuthGuard implements CanActivate {
  /**
   * @param reflector - NestJS 反射器，用于读取路由元数据（如 @Public 标记）
   * @param redis - Redis 服务，用于缓存 token_version
   * @param prisma - Prisma 数据库服务，用于查询用户 token_version
   */
  constructor(
    private reflector: Reflector,
    private redis: RedisService,
    private prisma: PrismaService,
  ) {}

  /**
   * 执行认证校验
   *
   * @param context - NestJS 执行上下文，包含请求信息和路由元数据
   * @returns true 表示认证通过，抛出 UnauthorizedException 表示认证失败
   * @throws UnauthorizedException 未登录、token 无效、token_version 不匹配时抛出
   */
  async canActivate(context: ExecutionContext): Promise<boolean> {
    const isPublic = this.reflector.getAllAndOverride<boolean>(IS_PUBLIC_KEY, [
      context.getHandler(),
      context.getClass(),
    ]);
    if (isPublic) return true;

    const request = context.switchToHttp().getRequest();
    const token = this.extractToken(request);
    if (!token) throw new UnauthorizedException('请登录');

    try {
      const payload = TokenUtil.verify(token);
      const { userId, tokenVersion, scope } = payload;

      // 根据 scope 查对应的用户表
      const appName = process.env.APP_NAME || 'app';
      const versionKey = `${appName}:TOKEN_VER:${scope}:${userId}`;
      let currentVersion = await this.redis.get(versionKey);

      if (currentVersion === null) {
        const user = await this.findUser(scope, userId);
        if (!user) throw new UnauthorizedException('用户不存在');
        currentVersion = String(user.tokenVersion);
        await this.redis.set(versionKey, currentVersion, 86400);
      }

      if (Number(currentVersion) !== tokenVersion) {
        throw new UnauthorizedException('登录已失效，请重新登录');
      }

      request.user = payload;
      return true;
    } catch (e) {
      if (e instanceof UnauthorizedException) throw e;
      throw new UnauthorizedException('token 无效');
    }
  }

  /**
   * 根据 scope 查询对应用户表
   *
   * @param scope - 用户类型：'admin' 查 admin_users 表，其他查 users 表
   * @param userId - 用户 ID
   * @returns 用户记录或 null
   */
  private async findUser(scope: string, userId: number) {
    if (scope === 'admin') {
      return this.prisma.adminUser.findUnique({ where: { id: userId } });
    }
    return this.prisma.user.findUnique({ where: { id: userId } });
  }

  /**
   * 从请求头中提取 Bearer token
   *
   * @param request - HTTP 请求对象
   * @returns token 字符串，格式不正确或不存在时返回 null
   */
  private extractToken(request: any): string | null {
    const auth = request.headers?.authorization;
    if (!auth) return null;
    const [type, token] = auth.split(' ');
    return type === 'Bearer' ? token : null;
  }
}
