import { get, post } from '../request'

export default {
  login:        (data: any) => post('/admin/user/login', data, { loading: true }),
  refreshToken: (data: any) => post('/admin/user/refreshToken', data, { showError: false }),
  logout:       ()          => post('/admin/user/logout'),
  info:         ()          => get('/admin/user/info'),
  menus:        ()          => get('/admin/user/menus', {}, { showError: false }),
  updateProfile:(data: any) => post('/admin/user/updateProfile', data),
  changePassword:(data: any)=> post('/admin/user/changePassword', data),
}
