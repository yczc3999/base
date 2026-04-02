import { get, post } from '../request'
import axios from 'axios'
import { getToken } from '@/utils/auth'

const BASE = import.meta.env.VITE_API_BASE_URL || ''
const PREFIX = import.meta.env.VITE_API_PREFIX || '/api'

export default {
  getList:     (params: any) => get('/admin/file/getList', params),
  getDetail:   (id: number)  => get('/admin/file/getDetail', { id }),
  doDelete:    (ids: any)    => post('/admin/file/batchDelete', { ids }),

  /** 上传文件 */
  upload(file: File, options: { category?: string; is_private?: boolean; onProgress?: (pct: number) => void } = {}) {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('category', options.category || 'default')
    formData.append('is_private', String(options.is_private || false))

    return axios.post(`${BASE}${PREFIX}/admin/file/upload`, formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
        'Authorization': `Bearer ${getToken()}`,
      },
      onUploadProgress: (e) => {
        if (e.total && options.onProgress) {
          options.onProgress(Math.round((e.loaded / e.total) * 100))
        }
      },
    }).then(res => {
      if (res.data.code === 0) return res.data.data
      throw new Error(res.data.msg || '上传失败')
    })
  },

  /** 上传图片 */
  uploadImage(file: File, options: { category?: string; is_private?: boolean; onProgress?: (pct: number) => void } = {}) {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('category', options.category || 'avatar')
    formData.append('is_private', String(options.is_private || false))

    return axios.post(`${BASE}${PREFIX}/admin/file/uploadImage`, formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
        'Authorization': `Bearer ${getToken()}`,
      },
      onUploadProgress: (e) => {
        if (e.total && options.onProgress) {
          options.onProgress(Math.round((e.loaded / e.total) * 100))
        }
      },
    }).then(res => {
      if (res.data.code === 0) return res.data.data
      throw new Error(res.data.msg || '上传失败')
    })
  },
}
