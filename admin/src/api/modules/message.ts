import { get, post } from '../request'

export default {
  getList:     (params: any) => get('/admin/message/getList', params),
  doDelete:    (ids: any)    => post('/admin/message/doDelete', { ids }),
  unreadCount: ()            => get('/admin/message/unreadCount', {}, { showError: false }),
  markRead:    (id: number)  => post('/admin/message/markRead', { id }),
}
