import { Get, Post, Body, Query, Req } from '@nestjs/common';
import { BaseController } from './BaseController.js';
import { BaseLogic } from '../logics/BaseLogic.js';

/**
 * CRUD 基础控制器
 *
 * 继承 BaseController，提供标准的增删改查 HTTP 接口。
 * 子类只需在构造函数中注入对应的 Logic 实例，即自动拥有
 * getList / getDetail / doEdit / doDelete 四个接口。
 *
 * 支持 bindUserColumn 实现数据行级隔离：当请求上下文中
 * 存在 bindUser 信息时，自动在查询条件中注入用户 ID 过滤。
 */
export class CurdController extends BaseController {
  /** 业务逻辑层实例，由子类构造函数注入 */
  protected logic: BaseLogic;

  /** 用户绑定字段名，设置后启用数据行级隔离（如 'userId'） */
  protected bindUserColumn?: string;

  /**
   * @param logic - 业务逻辑层实例，提供 CRUD 操作能力
   */
  constructor(logic: BaseLogic) {
    super();
    this.logic = logic;
  }

  /**
   * 获取列表（分页查询）
   *
   * 支持分页、排序、筛选。filters 参数支持 JSON 字符串自动解析。
   * 当配置了 bindUserColumn 且请求中包含 bindUser 信息时，
   * 自动注入用户 ID 条件实现数据隔离。
   *
   * @param query - 查询参数，包含 page / pageSize / sortField / sortOrder / filters
   * @param req - HTTP 请求对象，携带 bindUser 上下文
   * @returns 成功响应，data 包含 { list, total }
   */
  @Get('getList')
  async getList(@Query() query: any, @Req() req: any) {
    try {
      const queryData = { ...query };

      // filters 可能是 JSON 字符串
      if (typeof queryData.filters === 'string') {
        try { queryData.filters = JSON.parse(queryData.filters); } catch { queryData.filters = {}; }
      }

      // bindUserId 数据行级隔离
      if (req.bindUser?.shouldBind && this.bindUserColumn) {
        queryData.filters = queryData.filters || {};
        queryData.filters[this.bindUserColumn] = req.bindUser.userId;
      }

      const result = await this.logic.getList(queryData);
      return this.success(result);
    } catch (e) {
      return this.handleException(e);
    }
  }

  /**
   * 获取单条详情
   *
   * 根据主键查询记录。当配置了 bindUserColumn 时，
   * 额外校验记录归属，防止越权访问。
   *
   * @param query - 查询参数，需包含主键字段（如 id）
   * @param req - HTTP 请求对象，携带 bindUser 上下文
   * @returns 成功响应包含记录详情，记录不存在时返回失败响应
   */
  @Get('getDetail')
  async getDetail(@Query() query: any, @Req() req: any) {
    try {
      const primaryKey = this.logic.getPrimaryKeyName();
      const primaryValue = query[primaryKey];
      if (!primaryValue) return this.fail('缺少主键参数');

      // bindUserId 隔离
      if (req.bindUser?.shouldBind && this.bindUserColumn) {
        const conditions: Record<string, any> = { [primaryKey]: primaryValue };
        conditions[this.bindUserColumn] = req.bindUser.userId;
        const record = await this.logic.getDetail(conditions);
        return record ? this.success(record) : this.fail('数据不存在');
      }

      const record = await this.logic.getDetail(primaryValue);
      return record ? this.success(record) : this.fail('数据不存在');
    } catch (e) {
      return this.handleException(e);
    }
  }

  /**
   * 新增或编辑记录
   *
   * 当请求体包含主键值时执行更新，否则执行创建。
   * 统一由 Logic 层的 createOrUpdate 方法处理。
   *
   * @param body - 请求体，包含记录字段数据；含主键时为更新，不含时为创建
   * @returns 成功响应包含创建/更新后的完整记录
   */
  @Post('doEdit')
  async doEdit(@Body() body: any) {
    try {
      const result = await this.logic.createOrUpdate(body);
      return this.success(result);
    } catch (e) {
      return this.handleException(e);
    }
  }

  /**
   * 删除记录（支持批量）
   *
   * 接收 ids 参数，支持逗号分隔字符串、数组或单个值。
   *
   * @param body - 请求体，需包含 ids 字段（支持 string / number[] / number）
   * @returns 删除成功响应
   */
  @Post('doDelete')
  async doDelete(@Body() body: any) {
    try {
      const ids = body.ids;
      if (!ids) return this.fail('缺少 ids 参数');

      const idList = typeof ids === 'string' ? ids.split(',').map(Number) : Array.isArray(ids) ? ids : [ids];
      await this.logic.doDelete(idList);
      return this.success(null, '删除成功');
    } catch (e) {
      return this.handleException(e);
    }
  }
}
