import { Module } from '@nestjs/common';
import { RouterModule } from '@nestjs/core';
import { AdminModule } from '../controllers/admin/admin.module.js';

/**
 * 管理员后台路由
 *
 * 前缀：/api/admin
 * 实际路由：
 *   /api/admin/user/login
 *   /api/admin/user/getList
 *   /api/admin/user/getDetail
 *   /api/admin/user/doEdit
 *   /api/admin/user/doDelete
 *   /api/admin/user/info
 *   /api/admin/user/changePassword
 *   /api/admin/setting/getList
 *   /api/admin/setting/get
 *   /api/admin/setting/set
 *   ...
 */
@Module({
  imports: [
    AdminModule,
    RouterModule.register([
      {
        path: 'admin',
        module: AdminModule,
      },
    ]),
  ],
})
export class AdminRouteModule {}
