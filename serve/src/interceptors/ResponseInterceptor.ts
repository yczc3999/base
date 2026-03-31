import { Injectable, NestInterceptor, ExecutionContext, CallHandler } from '@nestjs/common';
import { Observable, map } from 'rxjs';

/**
 * 统一响应拦截器
 *
 * 全局注册，在 Controller 方法返回后对响应数据进行包装。
 *
 * 处理逻辑：
 * - 如果返回数据已包含 code 和 msg 字段（说明走了 BaseController 的 success/fail），直接透传
 * - 否则自动包装为 { code: 0, msg: 'success', data } 格式
 *
 * 这样即使 Controller 直接 return 原始数据，前端也能收到统一格式的响应。
 */
@Injectable()
export class ResponseInterceptor implements NestInterceptor {
  /**
   * 拦截处理方法
   *
   * @param _context - NestJS 执行上下文（当前未使用）
   * @param next - 调用链处理器，调用 next.handle() 执行后续的 Controller 方法
   * @returns Observable，通过 pipe(map) 对响应数据进行统一格式包装
   */
  intercept(_context: ExecutionContext, next: CallHandler): Observable<any> {
    return next.handle().pipe(
      map((data) => {
        if (data && typeof data === 'object' && 'code' in data && 'msg' in data) {
          return data;
        }
        return { code: 0, msg: 'success', data: data ?? null };
      }),
    );
  }
}
