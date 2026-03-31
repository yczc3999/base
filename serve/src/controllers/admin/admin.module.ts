import { Module } from '@nestjs/common';
import { APP_INTERCEPTOR } from '@nestjs/core';
import { AdminUserController } from './AdminUserController.js';
import { SettingController } from './SettingController.js';
import { OperationLogInterceptor } from '../../interceptors/OperationLogInterceptor.js';

/**
 * Admin 端控制器模块
 *
 * 注册管理员后台的所有 Controller。
 * 挂载操作日志拦截器，自动记录所有管理员操作。
 */
@Module({
  controllers: [AdminUserController, SettingController],
  providers: [
    {
      provide: APP_INTERCEPTOR,
      useClass: OperationLogInterceptor,
    },
  ],
})
export class AdminModule {}
