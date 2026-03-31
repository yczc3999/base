import { Injectable, OnModuleInit, OnModuleDestroy } from '@nestjs/common';
import Redis from 'ioredis';

/**
 * Redis 缓存服务
 *
 * 封装 ioredis 客户端，作为 NestJS 全局可注入的服务。
 * 提供常用的 Redis 操作方法（get / set / del / exists / scan / expire / ttl / incr / decr）。
 * 自动管理连接生命周期：模块初始化时创建连接，模块销毁时断开连接。
 *
 * 连接参数从环境变量读取：REDIS_HOST / REDIS_PORT / REDIS_PASSWORD / REDIS_DB
 */
@Injectable()
export class RedisService implements OnModuleInit, OnModuleDestroy {
  /** ioredis 客户端实例 */
  private client: Redis;

  /**
   * 模块初始化时创建 Redis 连接
   *
   * 从环境变量读取连接配置，创建 ioredis 客户端。
   */
  onModuleInit() {
    this.client = new Redis({
      host: process.env.REDIS_HOST || 'localhost',
      port: Number(process.env.REDIS_PORT) || 6379,
      password: process.env.REDIS_PASSWORD || undefined,
      db: Number(process.env.REDIS_DB) || 0,
    });
  }

  /**
   * 模块销毁时断开 Redis 连接
   */
  onModuleDestroy() {
    this.client?.disconnect();
  }

  /**
   * 获取缓存值
   *
   * @param key - 缓存键名
   * @returns 缓存值字符串，不存在时返回 null
   */
  async get(key: string): Promise<string | null> {
    return this.client.get(key);
  }

  /**
   * 设置缓存值
   *
   * @param key - 缓存键名
   * @param value - 缓存值字符串
   * @param expireSeconds - 可选，过期时间（秒）；不传则永不过期
   */
  async set(key: string, value: string, expireSeconds?: number): Promise<void> {
    if (expireSeconds) {
      await this.client.set(key, value, 'EX', expireSeconds);
    } else {
      await this.client.set(key, value);
    }
  }

  /**
   * 删除缓存（支持单个或批量）
   *
   * @param key - 缓存键名或键名数组
   */
  async del(key: string | string[]): Promise<void> {
    const keys = Array.isArray(key) ? key : [key];
    if (keys.length > 0) {
      await this.client.del(...keys);
    }
  }

  /**
   * 检查缓存键是否存在
   *
   * @param key - 缓存键名
   * @returns 存在返回 true，不存在返回 false
   */
  async exists(key: string): Promise<boolean> {
    return (await this.client.exists(key)) === 1;
  }

  /**
   * 使用 SCAN 命令扫描匹配的缓存键
   *
   * 相比 KEYS 命令，SCAN 不会阻塞 Redis 服务器，适合生产环境使用。
   * 通过游标分批扫描，直到遍历完所有键。
   *
   * @param pattern - 匹配模式（如 'app:User:*'）
   * @param count - 每次扫描的建议返回数量，默认 100
   * @returns 匹配的所有缓存键数组
   */
  async scan(pattern: string, count = 100): Promise<string[]> {
    const result: string[] = [];
    let cursor = '0';
    do {
      const [nextCursor, keys] = await this.client.scan(cursor, 'MATCH', pattern, 'COUNT', count);
      cursor = nextCursor;
      result.push(...keys);
    } while (cursor !== '0');
    return result;
  }

  /**
   * 设置缓存键的过期时间
   *
   * @param key - 缓存键名
   * @param seconds - 过期时间（秒）
   */
  async expire(key: string, seconds: number): Promise<void> {
    await this.client.expire(key, seconds);
  }

  /**
   * 获取缓存键的剩余存活时间
   *
   * @param key - 缓存键名
   * @returns 剩余秒数；-1 表示永不过期，-2 表示键不存在
   */
  async ttl(key: string): Promise<number> {
    return this.client.ttl(key);
  }

  /**
   * 将缓存键的值递增 1
   *
   * @param key - 缓存键名
   * @returns 递增后的值
   */
  async incr(key: string): Promise<number> {
    return this.client.incr(key);
  }

  /**
   * 将缓存键的值递减 1
   *
   * @param key - 缓存键名
   * @returns 递减后的值
   */
  async decr(key: string): Promise<number> {
    return this.client.decr(key);
  }
}
