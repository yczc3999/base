import { Module } from '@nestjs/common';
import { RouterModule } from '@nestjs/core';

/**
 * 用户后台路由
 * 前缀：/api/client
 */

@Module({
  imports: [
    RouterModule.register([
      {
        path: 'client',
        children: [
          // { path: 'user', module: ClientUserModule },
        ],
      },
    ]),
  ],
})
export class ClientRouteModule {}
