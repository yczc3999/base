/**
 * 通用配置 API 工厂
 *
 * 基于 settings 表的 category + name 结构，封装配置读写。
 *
 * 用法：
 *   const smsApi = createSettingApi('sms')
 *   const all = await smsApi.getAll()          → { default: 'aliyun', aliyun: {...}, qcloud: {...} }
 *   await smsApi.set('aliyun', { key: 'xxx' }) → 写入 category=sms, name=aliyun
 *   await smsApi.setDefault('aliyun')          → 写入 category=sms, name=default, value=aliyun
 *   const val = await smsApi.get('aliyun')     → 读取单个
 */

import { get, post } from './request'

export interface SettingApi {
  /** 获取该 category 下所有配置 */
  getAll: () => Promise<Record<string, any>>
  /** 获取单个配置值 */
  get: (name: string) => Promise<any>
  /** 设置单个配置（value 为对象时自动 JSON 序列化） */
  set: (name: string, value: any) => Promise<void>
  /** 批量设置 */
  setMany: (items: Record<string, any>) => Promise<void>
  /** 设置默认值（写入 name=default） */
  setDefault: (value: string) => Promise<void>
}

export function createSettingApi(category: string): SettingApi {
  return {
    async getAll() {
      const all = await get('/admin/setting/get')
      return all?.[category] || {}
    },

    async get(name: string) {
      const all = await get('/admin/setting/get', {}, { showError: false })
      return all?.[category]?.[name] ?? null
    },

    async set(name: string, value: any) {
      const val = typeof value === 'object' ? JSON.stringify(value) : String(value)
      await post('/admin/setting/set', { [category]: { [name]: val } })
    },

    async setMany(items: Record<string, any>) {
      const data: Record<string, string> = {}
      for (const [k, v] of Object.entries(items)) {
        data[k] = typeof v === 'object' ? JSON.stringify(v) : String(v)
      }
      await post('/admin/setting/set', { [category]: data })
    },

    async setDefault(value: string) {
      await post('/admin/setting/set', { [category]: { default: value } })
    },
  }
}
