import { get, post } from '../request'

export default {
  get: ()          => get('/admin/setting/get'),
  set: (data: any) => post('/admin/setting/set', data),
  /** 公开接口: 获取站点基础信息, 无需登录 */
  getSite: ()      => get('/admin/setting/site'),
}
