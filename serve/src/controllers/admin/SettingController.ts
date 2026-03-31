import { Controller, Get, Post, Body, Query } from '@nestjs/common';
import { CurdController } from '../CurdController.js';
import { SettingLogic } from '../../logics/SettingLogic.js';
import { Public } from '../../decorators/Public.js';
import { SettingSetDto } from './dto/SettingSetDto.js';

/**
 * 系统配置控制器
 *
 * 继承 CurdController，自动拥有 getList / getDetail / doEdit / doDelete。
 * 扩展 get/set 接口供前端读写配置。
 */
@Controller('setting')
export class SettingController extends CurdController {
  constructor(private readonly settingLogic: SettingLogic) {
    super(settingLogic);
  }

  /**
   * 读取配置（基础配置无需认证）
   *
   * @param query - { category, key? }
   * @returns 配置值
   */
  @Public()
  @Get('get')
  async getConfig(@Query() query: { category: string; key?: string }) {
    try {
      if (!query.category) return this.fail('缺少 category 参数');
      const result = await this.settingLogic.get(query.category, query.key);
      return this.success(result);
    } catch (e) {
      return this.handleException(e);
    }
  }

  /**
   * 写入配置
   *
   * @param body - SettingSetDto { category, key, value }
   */
  @Post('set')
  async setConfig(@Body() body: SettingSetDto) {
    try {
      await this.settingLogic.set(body.category, body.key, body.value);
      return this.success(null, '保存成功');
    } catch (e: any) {
      return this.fail(e.message);
    }
  }
}
