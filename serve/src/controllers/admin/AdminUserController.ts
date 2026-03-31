import { Controller, Post, Body, Get, Req } from '@nestjs/common';
import { CurdController } from '../CurdController.js';
import { AdminUserLogic } from '../../logics/AdminUserLogic.js';
import { Public } from '../../decorators/Public.js';
import { LoginDto } from './dto/LoginDto.js';
import { ChangePasswordDto } from './dto/ChangePasswordDto.js';

/**
 * 管理员用户控制器
 *
 * 继承 CurdController，自动拥有 getList / getDetail / doEdit / doDelete。
 * 扩展登录、获取当前用户信息、修改密码等接口。
 */
@Controller('user')
export class AdminUserController extends CurdController {
  constructor(private readonly adminUserLogic: AdminUserLogic) {
    super(adminUserLogic);
  }

  /**
   * 管理员登录（无需认证）
   *
   * @param body - LoginDto { username, password }
   * @returns { token, user }
   */
  @Public()
  @Post('login')
  async login(@Body() body: LoginDto, @Req() req: any) {
    try {
      const ip = req.ip || req.headers?.['x-forwarded-for'] || '';
      const userAgent = req.headers?.['user-agent'] || '';
      const result = await this.adminUserLogic.login(body.username, body.password, ip, userAgent);
      return this.success(result);
    } catch (e: any) {
      return this.fail(e.message);
    }
  }

  /**
   * 获取当前登录用户信息
   *
   * @param req - 请求对象，包含 user payload
   * @returns 当前用户详情
   */
  @Get('info')
  async info(@Req() req: any) {
    try {
      const user = await this.adminUserLogic.getDetail(req.user.userId);
      return user ? this.success(user) : this.fail('用户不存在');
    } catch (e) {
      return this.handleException(e);
    }
  }

  /**
   * 修改密码
   *
   * @param body - ChangePasswordDto { oldPassword, newPassword }
   * @param req - 请求对象
   */
  @Post('changePassword')
  async changePassword(@Body() body: ChangePasswordDto, @Req() req: any) {
    try {
      await this.adminUserLogic.changePassword(req.user.userId, body.oldPassword, body.newPassword);
      return this.success(null, '密码修改成功，请重新登录');
    } catch (e: any) {
      return this.fail(e.message);
    }
  }
}
