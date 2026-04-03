/**
 * 通用 CRUD API 工厂
 *
 * 用法：
 *   const api = createCrudApi('admin/user')
 *   api.getList({ page: 1 })        → GET /api/admin/user/getList
 *   api.getDetail(1)                 → GET /api/admin/user/getDetail?id=1
 *   api.doEdit({ username: 'test' }) → POST /api/admin/user/doEdit
 *   api.doDelete([1, 2])             → POST /api/admin/user/doDelete
 */

import { get, post } from './request'
import type { ListData } from './types'

export interface CrudApi {
  getList: (params?: any) => Promise<ListData>
  getDetail: (id: number) => Promise<any>
  doEdit: (data: any) => Promise<any>
  doDelete: (ids: number | number[] | string) => Promise<any>
  doExport: (data?: any) => Promise<any>
}

export function createCrudApi(prefix: string): CrudApi {
  const base = `/${prefix}`
  return {
    getList:   (params?: any) => get(`${base}/getList`, params),
    getDetail: (id: number)   => get(`${base}/getDetail`, { id }),
    doEdit:    (data: any)    => post(`${base}/doEdit`, data),
    doDelete:  (ids: any)     => post(`${base}/doDelete`, { ids }),
    doExport:  (data?: any)   => post(`${base}/doExport`, data),
  }
}
