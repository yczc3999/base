import { Injectable, NestInterceptor, ExecutionContext, CallHandler } from '@nestjs/common';
import { Observable, tap } from 'rxjs';
import { AdminOperationLogLogic } from '../logics/AdminOperationLogLogic.js';

/**
 * 操作日志拦截器
 *
 * 仅用于 admin 端路由组，自动记录管理员的所有操作。
 * 在请求处理完毕后（tap），异步写入日志，不阻塞响应。
 *
 * 记录内容：操作人、模块、方法、URL、参数、IP、耗时、响应码
 */
@Injectable()
export class OperationLogInterceptor implements NestInterceptor {
  constructor(private readonly operationLogLogic: AdminOperationLogLogic) {}

  /**
   * 拦截处理
   *
   * @param context - 执行上下文
   * @param next - 调用链处理器
   * @returns Observable
   */
  intercept(context: ExecutionContext, next: CallHandler): Observable<any> {
    const request = context.switchToHttp().getRequest();
    const startTime = Date.now();

    // 提取模块名和方法名
    const controllerClass = context.getClass();
    const handler = context.getHandler();
    const module = controllerClass.name;
    const action = handler.name;

    return next.handle().pipe(
      tap((responseData) => {
        // 仅在有登录用户时记录
        if (!request.user?.userId) return;

        const duration = Date.now() - startTime;
        const statusCode = responseData?.code ?? 0;

        // 过滤敏感参数
        const params = this.sanitizeParams(request.body || request.query);

        this.operationLogLogic.record({
          userId: request.user.userId,
          username: request.user.username || '',
          module,
          action,
          method: request.method,
          url: request.url,
          params: JSON.stringify(params),
          ip: request.ip || request.headers?.['x-forwarded-for'] || '',
          userAgent: request.headers?.['user-agent'] || '',
          statusCode,
          duration,
        });
      }),
    );
  }

  /**
   * 过滤敏感参数
   *
   * 移除密码等字段，避免明文记录到日志表
   *
   * @param params - 原始请求参数
   * @returns 过滤后的参数
   */
  private sanitizeParams(params: Record<string, any>): Record<string, any> {
    if (!params || typeof params !== 'object') return {};
    const sanitized = { ...params };
    const sensitiveKeys = ['password', 'oldPassword', 'newPassword', 'token', 'secret'];
    for (const key of sensitiveKeys) {
      if (key in sanitized) sanitized[key] = '***';
    }
    return sanitized;
  }
}
