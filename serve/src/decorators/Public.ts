import { SetMetadata } from '@nestjs/common';

/**
 * 元数据键名常量
 *
 * 用于在路由元数据中标记该路由是否为公开路由。
 * AuthGuard 通过 Reflector 读取此键来判断是否跳过认证。
 */
export const IS_PUBLIC_KEY = 'isPublic';

/**
 * @Public() 装饰器
 *
 * 标记无需认证的路由（如登录、注册、获取基础配置等公开接口）。
 * 被标记的路由会被 AuthGuard 跳过 JWT 校验，允许匿名访问。
 *
 * 使用示例：
 * ```typescript
 * @Public()
 * @Post('login')
 * async login(@Body() body: LoginDto) { ... }
 * ```
 *
 * @returns MethodDecorator & ClassDecorator - 可用于方法或类级别
 */
export const Public = () => SetMetadata(IS_PUBLIC_KEY, true);
