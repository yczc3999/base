/**
 * 基础控制器
 *
 * 所有 Controller 继承此类，提供统一的响应格式方法。
 * 设计意图：将响应结构（code / msg / data）收敛到基类，
 * 子类只关注业务逻辑，无需重复构造响应对象。
 */
export class BaseController {
  /**
   * 构造标准响应对象
   *
   * @param code - 业务状态码，0 表示成功，非 0 表示失败
   * @param message - 提示信息
   * @param data - 响应数据，默认为 null
   * @returns 统一格式的响应对象 { code, msg, data }
   */
  protected response(code: number, message: string, data: any = null) {
    return { code, msg: message, data };
  }

  /**
   * 返回成功响应
   *
   * @param data - 响应数据，默认为 null
   * @param message - 成功提示信息，默认 'success'
   * @param code - 业务状态码，默认 0
   * @returns 成功响应对象
   */
  protected success(data: any = null, message = 'success', code = 0) {
    return this.response(code, message, data);
  }

  /**
   * 返回失败响应
   *
   * @param message - 失败提示信息，默认 'fail'
   * @param code - 业务状态码，默认 1
   * @param data - 附加数据，默认为 null
   * @returns 失败响应对象
   */
  protected fail(message = 'fail', code = 1, data: any = null) {
    return this.response(code, message, data);
  }

  /**
   * 统一异常处理
   *
   * 在调试模式（APP_DEBUG=true）下返回错误详情（message + stack），
   * 生产环境仅返回通用错误提示，避免泄露内部信息。
   *
   * @param error - 捕获到的异常对象
   * @returns 失败响应对象，调试模式包含堆栈信息
   */
  protected handleException(error: unknown) {
    const isDebug = process.env.APP_DEBUG === 'true';
    if (isDebug && error instanceof Error) {
      return this.fail(error.message, 1, {
        message: error.message,
        stack: error.stack,
      });
    }
    return this.fail('服务器内部错误');
  }
}
