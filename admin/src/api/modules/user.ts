import { get, post } from '../request'

export default {
  getList:     (params: any)  => get('/admin/user/getList', params),
  getDetail:   (id: number)   => get('/admin/user/getDetail', { id }),
  doEdit:      (data: any)    => post('/admin/user/doEdit', data),
  doDelete:    (ids: any)     => post('/admin/user/doDelete', { ids }),
  roleIds:     (userId: number) => get('/admin/user/roleIds', { user_id: userId }),
  assignRoles: (data: any)    => post('/admin/user/assignRoles', data),
}
