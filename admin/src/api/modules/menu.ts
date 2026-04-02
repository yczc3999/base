import { get, post } from '../request'

export default {
  getList:   (params: any) => get('/admin/menu/getList', params),
  getDetail: (id: number)  => get('/admin/menu/getDetail', { id }),
  doEdit:    (data: any)   => post('/admin/menu/doEdit', data),
  doDelete:  (ids: any)    => post('/admin/menu/doDelete', { ids }),
  tree:      ()            => get('/admin/menu/tree'),
}
