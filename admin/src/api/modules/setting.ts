import { get, post } from '../request'

export default {
  get: ()          => get('/admin/setting/get'),
  set: (data: any) => post('/admin/setting/set', data),
}
