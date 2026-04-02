import { get } from '../request'

export default {
  operationList: (params: any) => get('/admin/operationLog/getList', params),
  loginList:     (params: any) => get('/admin/loginLog/getList', params),
}
