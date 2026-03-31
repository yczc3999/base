import { ExceptionFilter, Catch, ArgumentsHost, HttpException, HttpStatus } from '@nestjs/common';
import { Response } from 'express';

/**
 * 全局异常过滤器
 *
 * 捕获所有未被 Controller 层 try-catch 处理的异常，统一转换为标准响应格式。
 *
 * 设计策略：
 * - HTTP 层永远返回 200 状态码，业务错误通过 code 字段区分
 * - HttpException 提取原始状态码和消息
 * - 其他异常统一返回 500 + "服务器内部错误"
 * - 调试模式（APP_DEBUG=true）下返回错误详情（message + stack），方便排查问题
 * - 生产环境隐藏内部错误信息，避免信息泄露
 */
@Catch()
export class GlobalExceptionFilter implements ExceptionFilter {
  /**
   * 异常处理方法
   *
   * @param exception - 捕获到的异常对象（可能是 HttpException 或任意 Error）
   * @param host - NestJS 参数宿主，用于获取 HTTP 上下文（Request / Response）
   */
  catch(exception: unknown, host: ArgumentsHost) {
    const ctx = host.switchToHttp();
    const response = ctx.getResponse<Response>();
    const isDebug = process.env.APP_DEBUG === 'true';

    let code = HttpStatus.INTERNAL_SERVER_ERROR;
    let msg = '服务器内部错误';

    if (exception instanceof HttpException) {
      code = exception.getStatus();
      const res = exception.getResponse();
      msg = typeof res === 'string' ? res : (res as any).message || exception.message;
      if (Array.isArray(msg)) msg = msg[0];
    }

    const data = isDebug && exception instanceof Error
      ? { message: exception.message, stack: exception.stack }
      : null;

    response.status(200).json({ code, msg, data });
  }
}
