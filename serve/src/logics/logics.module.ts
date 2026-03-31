import { Global, Module } from '@nestjs/common';
import { AdminUserLogic } from './AdminUserLogic.js';
import { UserLogic } from './UserLogic.js';
import { SettingLogic } from './SettingLogic.js';
import { AdminOperationLogLogic } from './AdminOperationLogLogic.js';
import { AdminLoginLogLogic } from './AdminLoginLogLogic.js';

/**
 * 逻辑层全局模块
 *
 * 所有 Logic 注册为全局可注入，任何 Controller 均可直接使用。
 */
@Global()
@Module({
  providers: [AdminUserLogic, UserLogic, SettingLogic, AdminOperationLogLogic, AdminLoginLogLogic],
  exports: [AdminUserLogic, UserLogic, SettingLogic, AdminOperationLogLogic, AdminLoginLogLogic],
})
export class LogicsModule {}
