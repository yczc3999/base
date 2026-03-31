import { Injectable } from '@nestjs/common';
import { PrismaService } from '../services/PrismaService.js';
import { RedisService } from '../services/RedisService.js';
import { BaseLogic } from './BaseLogic.js';

/**
 * 系统配置逻辑层
 *
 * 双重身份：
 * 1. 继承 BaseLogic — 管理后台通过 CurdController 进行 CRUD 管理
 * 2. 扩展 get/set — 程序内部通过静态式方法读写配置
 *
 * 缓存策略：
 * - 不走 BaseLogic 的单条缓存，使用全量缓存模式
 * - 整张表平铺为 { category: { key: value } } 结构，一次 Redis 读取拿到全部配置
 * - 任何写操作后立即 makeCache() 重建全量缓存
 * - TTL 365 天（近乎永久），写入时主动刷新
 */
@Injectable()
export class SettingLogic extends BaseLogic {
  /** 全量缓存 TTL：365 天 */
  private static readonly CACHE_TTL = 86400 * 365;

  /** 静态引用，供 get/set 静态方法使用 */
  private static instance: SettingLogic;

  constructor(
    private readonly prisma: PrismaService,
    private readonly redisService: RedisService,
  ) {
    super(prisma.setting, redisService);
    // 不走 BaseLogic 的单条缓存
    this.needCache = false;
    // 注册静态引用
    SettingLogic.instance = this;
  }

  // ==================== 全量缓存读写 ====================

  /**
   * 获取缓存 key
   *
   * @returns 全量缓存 key 字符串
   */
  private getSettingsCacheKey(): string {
    const appName = process.env.APP_NAME || 'app';
    return `${appName}:SETTINGS`;
  }

  /**
   * 读取配置
   *
   * @param category - 配置类别（如 'site', 'sms', 'payment'）
   * @param key - 配置键名，不传则返回整个类别
   * @returns 配置值（自动 JSON 反序列化），不存在时返回 null 或空对象
   */
  async get(category: string, key?: string): Promise<any> {
    const cacheKey = this.getSettingsCacheKey();
    let cached = await this.redisService.get(cacheKey);

    if (!cached) {
      await this.makeCache();
      cached = await this.redisService.get(cacheKey);
    }

    if (!cached) return key ? null : {};

    const all = JSON.parse(cached);
    if (key) return all[category]?.[key] ?? null;
    return all[category] ?? {};
  }

  /**
   * 写入配置（upsert）
   *
   * @param category - 配置类别
   * @param key - 配置键名
   * @param value - 配置值（自动 JSON 序列化，支持任意类型）
   * @throws Error category/key 格式非法时抛出
   */
  async set(category: string, key: string, value: any): Promise<void> {
    if (!this.isValidInput(category) || !this.isValidInput(key)) {
      throw new Error('Invalid category or key');
    }

    const serialized = (value !== null && value !== undefined && typeof value === 'object')
      ? JSON.stringify(value)
      : String(value ?? '');

    await this.prisma.setting.upsert({
      where: { category_name: { category, name: key } },
      update: { value: serialized },
      create: { category, name: key, value: serialized },
    });

    await this.makeCache();
  }

  /**
   * 重建全量缓存
   *
   * 查询全表 → 按 category 分组 → JSON 序列化 → 写入 Redis
   */
  async makeCache(): Promise<void> {
    const cacheKey = this.getSettingsCacheKey();
    await this.redisService.del(cacheKey);

    const settings = await this.prisma.setting.findMany();
    if (!settings.length) return;

    const grouped: Record<string, Record<string, any>> = {};
    for (const s of settings) {
      if (!grouped[s.category]) grouped[s.category] = {};
      // 尝试 JSON.parse 还原
      let val: any = s.value;
      try { val = JSON.parse(s.value); } catch { /* 保留原值 */ }
      grouped[s.category][s.name] = val;
    }

    await this.redisService.set(cacheKey, JSON.stringify(grouped), SettingLogic.CACHE_TTL);
  }

  /**
   * 覆写 createOrUpdate — 写入后刷新全量缓存
   *
   * @param data - 记录数据
   * @returns 创建/更新后的记录
   */
  async createOrUpdate(data: Record<string, any>): Promise<Record<string, any>> {
    const result = await super.createOrUpdate(data);
    await this.makeCache();
    return result;
  }

  /**
   * 覆写 doDelete — 删除后刷新全量缓存
   *
   * @param primaryValueOrList - 主键值或数组
   */
  async doDelete(primaryValueOrList: any): Promise<void> {
    await super.doDelete(primaryValueOrList);
    await this.makeCache();
  }

  /**
   * 校验 category/key 格式
   * 仅允许字母、数字、下划线、横杠、点
   *
   * @param input - 待校验的字符串
   * @returns 是否合法
   */
  private isValidInput(input: string): boolean {
    return /^[a-zA-Z0-9_\-.]+$/.test(input);
  }

  // ==================== 静态便捷方法 ====================

  /**
   * 静态读取配置（便捷调用）
   *
   * @param category - 配置类别
   * @param key - 配置键名
   * @returns 配置值
   */
  static async getConfig(category: string, key?: string): Promise<any> {
    return SettingLogic.instance?.get(category, key) ?? null;
  }

  /**
   * 静态写入配置（便捷调用）
   *
   * @param category - 配置类别
   * @param key - 配置键名
   * @param value - 配置值
   */
  static async setConfig(category: string, key: string, value: any): Promise<void> {
    await SettingLogic.instance?.set(category, key, value);
  }
}
