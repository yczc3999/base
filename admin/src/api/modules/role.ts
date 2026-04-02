import { get, post } from '../request'

export default {
  getList:      (params: any) => get('/admin/role/getList', params),
  getDetail:    (id: number)  => get('/admin/role/getDetail', { id }),
  doEdit:       (data: any)   => post('/admin/role/doEdit', data),
  doDelete:     (ids: any)    => post('/admin/role/doDelete', { ids }),
  menuIds:      (roleId: number) => get('/admin/role/menuIds', { role_id: roleId }),
  assignMenus:  (data: any)   => post('/admin/role/assignMenus', data),
}
