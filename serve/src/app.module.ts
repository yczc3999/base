import { Module } from '@nestjs/common';
import { APP_GUARD } from '@nestjs/core';
import { ServicesModule } from './services/services.module.js';
import { LogicsModule } from './logics/logics.module.js';
import { AdminRouteModule } from './routes/admin.js';
import { ClientRouteModule } from './routes/client.js';
import { AuthGuard } from './guards/AuthGuard.js';

@Module({
  imports: [
    ServicesModule,
    LogicsModule,
    AdminRouteModule,
    ClientRouteModule,
  ],
  providers: [
    {
      provide: APP_GUARD,
      useClass: AuthGuard,
    },
  ],
})
export class AppModule {}
