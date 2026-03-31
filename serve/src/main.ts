import 'dotenv/config';
import { NestFactory } from '@nestjs/core';
import { ValidationPipe } from '@nestjs/common';
import { AppModule } from './app.module.js';
import { ResponseInterceptor } from './interceptors/ResponseInterceptor.js';
import { GlobalExceptionFilter } from './filters/GlobalExceptionFilter.js';

async function bootstrap() {
  const app = await NestFactory.create(AppModule);

  // 全局前缀
  app.setGlobalPrefix('api');

  // 全局验证管道（配合 class-validator DTO）
  app.useGlobalPipes(
    new ValidationPipe({
      whitelist: true,
      transform: true,
    }),
  );

  // 统一响应格式
  app.useGlobalInterceptors(new ResponseInterceptor());

  // 统一异常处理
  app.useGlobalFilters(new GlobalExceptionFilter());

  // CORS
  app.enableCors();

  const port = process.env.PORT ?? 3000;
  await app.listen(port);
  console.log(`Server running on http://localhost:${port}`);
}
bootstrap();
