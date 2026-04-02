import axios, { type AxiosRequestConfig, type InternalAxiosRequestConfig } from 'axios'
import { ElMessage, ElLoading } from 'element-plus'
import { getToken, getRefreshToken, setToken, clearTokens } from '@/utils/auth'
import type { ApiResponse, RequestOptions } from './types'

const BASE_URL = import.meta.env.VITE_API_BASE_URL || ''
const PREFIX = import.meta.env.VITE_API_PREFIX || '/api'

const instance = axios.create({
  baseURL: BASE_URL + PREFIX,
  timeout: 15000,
  headers: { 'Content-Type': 'application/json' },
})

// ── loading 管理 ──
let loadingCount = 0
let loadingInstance: any = null

function showLoading() {
  if (loadingCount === 0) {
    loadingInstance = ElLoading.service({ fullscreen: true, background: 'rgba(0,0,0,0.1)' })
  }
  loadingCount++
}

function hideLoading() {
  loadingCount--
  if (loadingCount <= 0) {
    loadingCount = 0
    loadingInstance?.close()
    loadingInstance = null
  }
}

// ── refresh 锁 ──
let isRefreshing = false
let pendingRequests: Array<(token: string) => void> = []

async function doRefresh(): Promise<string | null> {
  const refreshToken = getRefreshToken()
  if (!refreshToken) return null
  try {
    const { data } = await axios.post(`${BASE_URL}${PREFIX}/admin/user/refreshToken`, {
      refresh_token: refreshToken,
    })
    if (data.code === 0 && data.data?.access_token) {
      setToken(data.data.access_token)
      return data.data.access_token
    }
  } catch {}
  return null
}

// ── 请求拦截器 ──
instance.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = getToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// ── 响应拦截器 ──
instance.interceptors.response.use(
  (response) => response,
  (error) => Promise.reject(error),
)

// ── 统一请求方法 ──
async function request<T = any>(
  config: AxiosRequestConfig,
  options: RequestOptions = {},
): Promise<T> {
  const { loading = false, showError = true, retry = true } = options

  if (loading) showLoading()

  try {
    const response = await instance(config)
    const res: ApiResponse<T> = response.data

    // 成功
    if (res.code === 0) {
      return res.data
    }

    // 401 — 尝试 refresh
    if (res.code === 401 && retry) {
      if (!isRefreshing) {
        isRefreshing = true
        const newToken = await doRefresh()
        isRefreshing = false

        if (newToken) {
          // 重试所有等待的请求
          pendingRequests.forEach((cb) => cb(newToken))
          pendingRequests = []
          // 重试当前请求
          config.headers = { ...config.headers, Authorization: `Bearer ${newToken}` }
          return request<T>(config, { ...options, retry: false })
        } else {
          // refresh 也失败了，清除登录态
          clearTokens()
          pendingRequests = []
          window.location.hash = '#/login'
          throw new Error('请登录')
        }
      } else {
        // 正在刷新中，排队等待
        return new Promise((resolve) => {
          pendingRequests.push((token: string) => {
            config.headers = { ...config.headers, Authorization: `Bearer ${token}` }
            resolve(request<T>(config, { ...options, retry: false }))
          })
        })
      }
    }

    // 其他错误
    if (showError) {
      ElMessage.error(res.msg || '操作失败')
    }
    throw new Error(res.msg || '操作失败')
  } catch (error: any) {
    if (showError && !error.message?.includes('请登录')) {
      const msg = error.response?.data?.msg || error.message || '网络错误'
      ElMessage.error(msg)
    }
    throw error
  } finally {
    if (loading) hideLoading()
  }
}

// ── 导出快捷方法 ──
export function get<T = any>(url: string, params?: any, options?: RequestOptions): Promise<T> {
  return request<T>({ method: 'GET', url, params }, options)
}

export function post<T = any>(url: string, data?: any, options?: RequestOptions): Promise<T> {
  return request<T>({ method: 'POST', url, data }, options)
}

export default instance
