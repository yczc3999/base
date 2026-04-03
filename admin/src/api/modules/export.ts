/**
 * 导出 API
 *
 * 进度轮询 + 文件下载（Blob）
 */

import { get } from '../request'
import instance from '../request'

/** 查询导出进度 */
export function getExportProgress(key: string) {
  return get<{ progress: number; status: number }>('/admin/export/progress', { key })
}

/** 下载导出文件（Blob 方式，自动触发浏览器下载） */
export async function downloadExportFile(key: string) {
  const response = await instance.get('/admin/export/download', {
    params: { key },
    responseType: 'blob',
  })

  // 从 Content-Disposition 提取文件名
  const disposition = response.headers['content-disposition'] || ''
  const match = disposition.match(/filename\*?=(?:UTF-8'')?(.+)/i)
  const filename = match ? decodeURIComponent(match[1]) : 'export.xlsx'

  // 触发浏览器下载
  const url = window.URL.createObjectURL(response.data)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  window.URL.revokeObjectURL(url)
}
