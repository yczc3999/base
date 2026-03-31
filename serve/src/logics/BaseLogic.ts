import { RedisService } from '../services/RedisService.js';

/**
 * 基础逻辑层
 *
 * 设计哲学：
 * - 子类只需传入 modelDelegate（Prisma 表操作对象），即拥有完整 CRUD + 缓存 + 格式化
 * - 缓存、脱敏等横切关注点，调用方无需判断开关，底层方法自行决定是否生效
 * - 子类通过 override 扩展，不修改父类
 *
 * 泛型参数 T 表示 Prisma 模型类型（预留，方便后续类型收窄）。
 *
 * 核心能力：
 * 1. CRUD 操作：getList / getDetail / createOrUpdate / doCreate / doEdit / doDelete / doUpdate
 * 2. 多字段缓存：支持按主键和自定义字段缓存，编辑时自动清理旧缓存并写入新缓存
 * 3. 数据格式化：入库方向（toJson）和输出方向（fromJson + 脱敏）
 * 4. 生命周期钩子：beforeCreate / beforeEdit / beforeDelete / afterDelete
 */
export class BaseLogic<T = any> {
  // ===== 模型相关 =====

  /** Prisma 表操作委托对象（如 prisma.user），由子类构造函数传入 */
  protected modelDelegate: any;

  /** 主键字段名，默认 'id' */
  protected primaryKeyName = 'id';

  // ===== 缓存相关 =====

  /** 是否启用缓存，默认关闭 */
  protected needCache = false;

  /** 缓存 key 前缀，默认取类名去掉 Logic 后缀 */
  protected cachePrefix = '';

  /** 缓存过期时间（秒），默认 86400（24 小时） */
  protected cacheExpire = 86400;

  /** 需要建立缓存索引的字段列表（如 ['email', 'phone']），支持按这些字段从缓存查询 */
  protected cacheKeys: string[] = [];

  // ===== 格式化相关 =====

  /** 输出时需要过滤的敏感字段列表（如 ['password', 'salt']） */
  protected exceptKeys: string[] = [];

  // ===== 基础设施 =====

  /** Redis 服务实例，用于缓存操作 */
  protected redis: RedisService;

  /**
   * @param modelDelegate - Prisma 表操作委托对象（如 prisma.user）
   * @param redis - Redis 服务实例
   */
  constructor(modelDelegate: any, redis: RedisService) {
    this.modelDelegate = modelDelegate;
    this.redis = redis;

    if (!this.cachePrefix) {
      this.cachePrefix = this.constructor.name.replace(/Logic$/, '');
    }
  }

  /**
   * 获取主键字段名
   *
   * @returns 主键字段名字符串
   */
  getPrimaryKeyName(): string {
    return this.primaryKeyName;
  }

  // ==================== CRUD ====================

  /**
   * 列表查询（分页）
   *
   * 支持分页、排序、筛选。筛选条件通过 buildWhere 转换为 Prisma where 子句。
   *
   * @param queryData - 查询参数对象
   * @param queryData.page - 页码，默认 1
   * @param queryData.pageSize - 每页条数，默认 20
   * @param queryData.sortField - 排序字段，默认主键
   * @param queryData.sortOrder - 排序方向 'asc' | 'desc'，默认 'desc'
   * @param queryData.filters - 筛选条件对象
   * @returns 包含 list（格式化后的记录数组）和 total（总记录数）的对象
   */
  async getList(queryData: Record<string, any> = {}) {
    const page = Number(queryData.page) || 1;
    const pageSize = Number(queryData.pageSize) || 20;
    const sortField = queryData.sortField || this.primaryKeyName;
    const sortOrder = queryData.sortOrder === 'asc' ? 'asc' : 'desc';
    const filters = queryData.filters || {};

    const where = this.buildWhere(filters);

    const [list, total] = await Promise.all([
      this.modelDelegate.findMany({
        where,
        skip: (page - 1) * pageSize,
        take: pageSize,
        orderBy: { [sortField]: sortOrder },
      }),
      this.modelDelegate.count({ where }),
    ]);

    return {
      list: this.formatList(list),
      total,
    };
  }

  /**
   * 详情查询
   *
   * 支持两种调用方式：
   * 1. 传入主键值 → 按主键查询，优先走缓存
   * 2. 传入条件对象 → 按条件查询，单字段条件且在 cacheKeys 中时走缓存
   *
   * @param primaryValueOrConditions - 主键值（number/string）或条件对象（Record<string, any>）
   * @param options - 可选配置
   * @param options.withSensitive - 是否保留敏感字段（默认 false，会过滤 exceptKeys 中的字段）
   * @returns 格式化后的记录对象，不存在时返回 null
   */
  async getDetail(
    primaryValueOrConditions: any,
    options: { withSensitive?: boolean } = {},
  ): Promise<Record<string, any> | null> {
    let record: any = null;

    if (typeof primaryValueOrConditions === 'object' && !Array.isArray(primaryValueOrConditions)) {
      // 条件查询 — 检查是否可走缓存
      const conditionKeys = Object.keys(primaryValueOrConditions);
      if (conditionKeys.length === 1) {
        const field = conditionKeys[0];
        const value = primaryValueOrConditions[field];
        if (this.cacheKeys.includes(field) || field === this.primaryKeyName) {
          record = await this.getFromCache(field, value);
          if (record) return this.formatOutput(record, options);
        }
      }
      // 条件查询
      const where = this.buildWhere(primaryValueOrConditions);
      record = await this.modelDelegate.findFirst({ where });
    } else {
      // 主键查询 — 走缓存
      record = await this.getFromCache(this.primaryKeyName, primaryValueOrConditions);
      if (record) return this.formatOutput(record, options);
      record = await this.modelDelegate.findUnique({
        where: { [this.primaryKeyName]: Number(primaryValueOrConditions) },
      });
    }

    if (record) {
      await this.createCache(record);
    }
    return record ? this.formatOutput(record, options) : null;
  }

  /**
   * 创建或更新（智能判断）
   *
   * 当 data 中包含主键值时执行更新，否则执行创建。
   *
   * @param data - 记录数据，包含主键时为更新，不包含时为创建
   * @returns 创建/更新后的完整记录（格式化后）
   */
  async createOrUpdate(data: Record<string, any>): Promise<Record<string, any>> {
    const primaryValue = data[this.primaryKeyName];
    if (primaryValue) {
      const { [this.primaryKeyName]: _, ...updateData } = data;
      await this.doEdit(primaryValue, updateData);
      return (await this.getDetail(primaryValue))!;
    }
    return this.doCreate(data);
  }

  /**
   * 创建记录
   *
   * 流程：formatSaveData → beforeCreate 钩子 → 入库 → 写缓存 → formatOutput
   *
   * @param data - 待创建的记录数据
   * @returns 创建后的完整记录（格式化后）
   */
  async doCreate(data: Record<string, any>): Promise<Record<string, any>> {
    let formatted = this.formatSaveData(data);
    formatted = this.beforeCreate(formatted);
    const record = await this.modelDelegate.create({ data: formatted });
    await this.createCache(record);
    return this.formatOutput(record);
  }

  /**
   * 编辑记录（多字段缓存安全更新）
   *
   * 流程：查旧记录 → formatSaveData → beforeEdit 钩子 → 入库更新
   * → 查新记录 → 清旧缓存 → 写新缓存
   *
   * 为什么要查旧记录：当 cacheKeys 中的字段值变化时（如 email 从 a@x 改为 b@x），
   * 需要清理旧值对应的缓存 key。
   *
   * @param primaryValue - 主键值
   * @param data - 待更新的字段数据
   * @returns 更新是否成功
   */
  async doEdit(primaryValue: any, data: Record<string, any>): Promise<boolean> {
    // 查旧记录（用于清理旧缓存 key）
    const oldRecord = await this.modelDelegate.findUnique({
      where: { [this.primaryKeyName]: Number(primaryValue) },
    });

    let formatted = this.formatSaveData(data);
    formatted = this.beforeEdit(formatted);

    await this.modelDelegate.update({
      where: { [this.primaryKeyName]: Number(primaryValue) },
      data: formatted,
    });

    // 查新记录，刷新缓存
    const newRecord = await this.modelDelegate.findUnique({
      where: { [this.primaryKeyName]: Number(primaryValue) },
    });

    if (oldRecord) await this.clearCache(oldRecord);
    if (newRecord) await this.createCache(newRecord);

    return true;
  }

  /**
   * 删除记录（支持单个或批量）
   *
   * 流程：遍历每个主键值 → beforeDelete 钩子 → 查记录 → 删除 → 清缓存 → afterDelete 钩子
   *
   * @param primaryValueOrList - 单个主键值或主键值数组
   */
  async doDelete(primaryValueOrList: any): Promise<void> {
    const values = Array.isArray(primaryValueOrList) ? primaryValueOrList : [primaryValueOrList];

    for (const primaryValue of values) {
      await this.beforeDelete(primaryValue);

      const record = await this.modelDelegate.findUnique({
        where: { [this.primaryKeyName]: Number(primaryValue) },
      });

      await this.modelDelegate.delete({
        where: { [this.primaryKeyName]: Number(primaryValue) },
      });

      if (record) await this.clearCache(record);

      await this.afterDelete(primaryValue);
    }
  }

  /**
   * 批量更新（不走缓存）
   *
   * 直接执行 Prisma updateMany，适用于批量状态变更等场景。
   * 注意：此方法不会触发缓存更新，调用方需自行处理缓存一致性。
   *
   * @param conditions - 筛选条件对象
   * @param data - 待更新的字段数据
   * @returns Prisma updateMany 的结果（包含 count）
   */
  async doUpdate(conditions: Record<string, any>, data: Record<string, any>) {
    const where = this.buildWhere(conditions);
    const formatted = this.formatSaveData(data);
    return this.modelDelegate.updateMany({ where, data: formatted });
  }

  // ==================== 生命周期钩子 ====================

  /**
   * 创建前钩子
   *
   * 子类可 override 此方法，在数据入库前进行加工（如密码加密、默认值填充）。
   *
   * @param data - 待创建的数据（已经过 formatSaveData 处理）
   * @returns 加工后的数据
   */
  protected beforeCreate(data: Record<string, any>): Record<string, any> {
    return data;
  }

  /**
   * 编辑前钩子
   *
   * 子类可 override 此方法，在数据更新前进行加工。
   *
   * @param data - 待更新的数据（已经过 formatSaveData 处理）
   * @returns 加工后的数据
   */
  protected beforeEdit(data: Record<string, any>): Record<string, any> {
    return data;
  }

  /**
   * 删除前钩子
   *
   * 子类可 override 此方法，在删除前执行校验或关联清理。
   *
   * @param _primaryValue - 即将删除的记录主键值
   */
  protected beforeDelete(_primaryValue: any): void {}

  /**
   * 删除后钩子
   *
   * 子类可 override 此方法，在删除后执行关联操作（如清理关联表数据）。
   *
   * @param _primaryValue - 已删除的记录主键值
   */
  protected afterDelete(_primaryValue: any): void {}

  // ==================== 缓存 ====================

  /**
   * 获取缓存 key 前缀
   *
   * 格式：{APP_NAME}:{cachePrefix}，如 "myapp:User"
   *
   * @returns 缓存 key 前缀字符串
   */
  private getCacheKeyPrefix(): string {
    const appName = process.env.APP_NAME || 'app';
    return `${appName}:${this.cachePrefix}`;
  }

  /**
   * 从缓存中获取记录
   *
   * 当 needCache 为 false 时直接返回 null（不走缓存逻辑）。
   *
   * @param field - 缓存索引字段名（如 'id' / 'email'）
   * @param value - 字段值
   * @returns 缓存中的记录对象，未命中或未启用缓存时返回 null
   */
  protected async getFromCache(field: string, value: any): Promise<Record<string, any> | null> {
    if (!this.needCache) return null;
    const key = `${this.getCacheKeyPrefix()}:${field}:${value}`;
    const cached = await this.redis.get(key);
    if (!cached) return null;
    try {
      return JSON.parse(cached);
    } catch {
      return null;
    }
  }

  /**
   * 写入缓存
   *
   * 为 cacheKeys 中的每个字段建立缓存索引。
   * 当 needCache 为 false 时跳过。
   *
   * @param record - 完整的数据库记录
   */
  protected async createCache(record: Record<string, any>): Promise<void> {
    if (!this.needCache) return;
    const keys = this.cacheKeys.length ? this.cacheKeys : [this.primaryKeyName];
    const json = JSON.stringify(record);
    for (const field of keys) {
      if (record[field] === undefined || record[field] === null) continue;
      const key = `${this.getCacheKeyPrefix()}:${field}:${record[field]}`;
      await this.redis.set(key, json, this.cacheExpire);
    }
  }

  /**
   * 清除记录对应的所有缓存
   *
   * 根据 cacheKeys 中的字段值，逐一删除对应的缓存 key。
   *
   * @param record - 需要清除缓存的数据库记录
   */
  protected async clearCache(record: Record<string, any>): Promise<void> {
    if (!this.needCache) return;
    const keys = this.cacheKeys.length ? this.cacheKeys : [this.primaryKeyName];
    for (const field of keys) {
      if (record[field] === undefined || record[field] === null) continue;
      const key = `${this.getCacheKeyPrefix()}:${field}:${record[field]}`;
      await this.redis.del(key);
    }
  }

  /**
   * 清除该 Logic 的全部缓存
   *
   * 使用 SCAN 替代 KEYS 命令，避免在生产环境中阻塞 Redis。
   * 适用于数据批量变更后需要全量刷新缓存的场景。
   */
  protected async rebuildCacheAll(): Promise<void> {
    if (!this.needCache) return;
    const pattern = `${this.getCacheKeyPrefix()}:*`;
    const keys = await this.redis.scan(pattern);
    if (keys.length > 0) {
      await this.redis.del(keys);
    }
  }

  // ==================== 数据格式化 ====================

  /**
   * 入库方向格式化
   *
   * 将数据对象中的数组和对象类型字段序列化为 JSON 字符串，
   * 以便存入数据库的文本字段。undefined 值会被跳过。
   *
   * @param data - 原始数据对象
   * @returns 格式化后的数据对象，数组/对象值已转为 JSON 字符串
   */
  protected formatSaveData(data: Record<string, any>): Record<string, any> {
    const result: Record<string, any> = {};
    for (const [key, value] of Object.entries(data)) {
      if (value !== undefined) {
        result[key] = this.toJson(value);
      }
    }
    return result;
  }

  /**
   * 输出方向格式化（单条记录）
   *
   * 1. 过滤 exceptKeys 中的敏感字段（除非 withSensitive 为 true）
   * 2. 将 JSON 字符串还原为对象/数组
   *
   * @param record - 数据库原始记录
   * @param options - 可选配置
   * @param options.withSensitive - 是否保留敏感字段
   * @returns 格式化后的记录对象
   */
  protected formatOutput(
    record: Record<string, any>,
    options: { withSensitive?: boolean } = {},
  ): Record<string, any> {
    const data = { ...record };

    // 过滤敏感字段
    if (!options.withSensitive && this.exceptKeys.length) {
      for (const key of this.exceptKeys) {
        delete data[key];
      }
    }

    // JSON 字符串还原
    for (const [key, value] of Object.entries(data)) {
      if (typeof value === 'string') {
        data[key] = this.fromJson(value);
      }
    }

    return data;
  }

  /**
   * 输出方向格式化（列表）
   *
   * 对列表中的每条记录调用 formatOutput 进行格式化。
   *
   * @param records - 数据库原始记录数组
   * @returns 格式化后的记录数组
   */
  protected formatList(records: Record<string, any>[]): Record<string, any>[] {
    return records.map((r) => this.formatOutput(r));
  }

  // ==================== 条件构建 ====================

  /**
   * 构建 Prisma where 条件
   *
   * 转换规则：
   * - 空值（undefined / null / ''）跳过，但保留 0 和 false
   * - 数组 → { in: [...] }
   * - 对象（非 Date）→ 透传 Prisma 操作符（如 { gte, lte, contains }）
   * - 普通值 → 直接等值匹配
   *
   * @param conditions - 筛选条件对象，key 为字段名，value 为筛选值
   * @returns Prisma 格式的 where 条件对象
   */
  protected buildWhere(conditions: Record<string, any>): Record<string, any> {
    const where: Record<string, any> = {};
    for (const [key, value] of Object.entries(conditions)) {
      if (value === undefined || value === null || value === '') continue;
      if (Array.isArray(value)) {
        where[key] = { in: value };
      } else if (typeof value === 'object' && !(value instanceof Date)) {
        // 透传 Prisma 操作符: { gte, lte, contains, gt, lt, not ... }
        where[key] = value;
      } else {
        where[key] = value;
      }
    }
    return where;
  }

  // ==================== 工具方法 ====================

  /**
   * 将值序列化为 JSON 字符串（入库方向）
   *
   * 仅对数组和普通对象进行序列化，null / undefined / Date / 基本类型直接返回。
   *
   * @param value - 待序列化的值
   * @returns 序列化后的值（字符串或原值）
   */
  private toJson(value: any): any {
    if (value === null || value === undefined) return value;
    if (Array.isArray(value) || (typeof value === 'object' && !(value instanceof Date))) {
      try {
        return JSON.stringify(value);
      } catch {
        return value;
      }
    }
    return value;
  }

  /**
   * 将 JSON 字符串反序列化为对象/数组（输出方向）
   *
   * 仅对以 { 或 [ 开头的字符串尝试解析，解析失败时返回原字符串。
   *
   * @param value - 待反序列化的字符串
   * @returns 解析后的对象/数组，或原字符串
   */
  private fromJson(value: string): any {
    if (!value || typeof value !== 'string') return value;
    const trimmed = value.trim();
    if ((trimmed.startsWith('{') && trimmed.endsWith('}')) || (trimmed.startsWith('[') && trimmed.endsWith(']'))) {
      try {
        return JSON.parse(trimmed);
      } catch {
        return value;
      }
    }
    return value;
  }
}
