import axios, {
  type AxiosRequestConfig,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from 'axios'
import { ElMessage } from 'element-plus/es/components/message/index'
import { getToken, getRefreshToken, setToken, clearTokens } from '@/utils/auth'
import type { ApiResponse, RequestOptions } from './types'

const BASE_URL = import.meta.env.VITE_API_BASE_URL || ''
const PREFIX = import.meta.env.VITE_API_PREFIX || '/api'

const instance = axios.create({
  baseURL: BASE_URL + PREFIX,
  timeout: 15000,
  headers: { 'Content-Type': 'application/json' },
})

// ── loading：顶部进度条 ──
import NProgress from '@/utils/nprogress'
let loadingCount = 0

function showLoading() {
  if (loadingCount === 0) NProgress.start()
  loadingCount++
}

function hideLoading() {
  loadingCount--
  if (loadingCount <= 0) {
    loadingCount = 0
    NProgress.done()
  }
}

// ── refresh 锁（Promise 共享方案，解决并发 401 卡死问题）──
let refreshPromise: Promise<string | null> | null = null

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
  } catch {
    // refresh 失败静默：走统一 401 重登逻辑
  }
  return null
}

function getRefreshPromise(): Promise<string | null> {
  if (!refreshPromise) {
    refreshPromise = doRefresh().finally(() => { refreshPromise = null })
  }
  return refreshPromise
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
export class ApiRequestError extends Error {
  code?: number
  status?: number

  constructor(message: string, code?: number, status?: number) {
    super(message)
    this.name = 'ApiRequestError'
    this.code = code
    this.status = status
  }
}

/** Axios、DOM AbortError 与测试/平台 ERR_CANCELED 使用同一取消判定。 */
export function isRequestCanceled(error: unknown): boolean {
  if (typeof axios.isCancel === 'function' && axios.isCancel(error)) return true
  if (!(error instanceof Error)) return false
  const candidate = error as Error & { code?: string }
  return candidate.name === 'AbortError' || candidate.name === 'CanceledError'
    || candidate.code === 'ERR_CANCELED'
}

function redirectToLogin(): void {
  clearTokens()
  if (typeof window !== 'undefined') window.location.hash = '#/login'
}

async function refreshOrFail(): Promise<string> {
  const newToken = await getRefreshPromise()
  if (newToken) return newToken
  redirectToLogin()
  throw new ApiRequestError('请登录', 401, 401)
}

function withAuthorization(config: AxiosRequestConfig, token: string): AxiosRequestConfig {
  return {
    ...config,
    headers: { ...config.headers, Authorization: `Bearer ${token}` },
  }
}

/**
 * 共享的 Axios dispatch：HTTP 401 与 envelope 401 都复用同一 refresh 锁。
 * 取消在 refresh/error UI 之前直接抛出，因此不 retry、不 toast。
 */
async function dispatch<T>(
  config: AxiosRequestConfig,
  retryAuth: boolean,
): Promise<AxiosResponse<T>> {
  try {
    return await instance(config)
  } catch (error: unknown) {
    if (isRequestCanceled(error)) throw error
    const status = (error as { response?: { status?: number } })?.response?.status
    if (status === 401) {
      if (retryAuth) {
        const token = await refreshOrFail()
        return dispatch<T>(withAuthorization(config, token), false)
      }
      redirectToLogin()
      throw new ApiRequestError('请登录', 401, 401)
    }
    throw error
  }
}

async function request<T = any>(
  config: AxiosRequestConfig,
  options: RequestOptions = {},
): Promise<T> {
  const { loading = false, showError = true, retry = true } = options

  if (loading) showLoading()

  try {
    const response = await dispatch<ApiResponse<T>>(config, retry)
    const res: ApiResponse<T> = response.data

    // 成功
    if (res.code === 0) {
      return res.data
    }

    // 401 — 所有并发请求共享同一个 refresh Promise
    if (res.code === 401) {
      if (retry) {
        const newToken = await refreshOrFail()
        return request<T>(withAuthorization(config, newToken), { ...options, retry: false })
      }
      redirectToLogin()
      throw new ApiRequestError('请登录', 401, 401)
    }

    throw new ApiRequestError(res.msg || '操作失败', res.code, response.status)
  } catch (error: unknown) {
    if (isRequestCanceled(error)) throw error
    const candidate = error as {
      code?: string
      message?: string
      response?: { status?: number; data?: { msg?: string } }
    }
    if (showError && !candidate.message?.includes('请登录')) {
      let msg: string
      if (candidate.response?.data?.msg) {
        msg = candidate.response.data.msg
      } else if (candidate.code === 'ECONNABORTED' || /timeout/i.test(candidate.message || '')) {
        msg = '请求超时，请重试'
      } else if (candidate.response?.status === 401) {
        msg = '请登录'
      } else if (candidate.response?.status === 403) {
        msg = '无权限执行此操作'
      } else if (candidate.response?.status === 404) {
        msg = '资源不存在'
      } else if ((candidate.response?.status ?? 0) >= 500) {
        msg = '服务异常，请稍后重试'
      } else if (candidate.message === 'Network Error') {
        msg = '网络异常，请检查连接'
      } else {
        msg = candidate.message || '操作失败'
      }
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

// =====================================================================
// WP-07A —— V2 Admin Read typed request（复用同一 axios instance/refresh，
// 支持 AbortSignal；不吞 401/403/contract errors，交给 query hooks）。
// =====================================================================


export interface V2RequestConfig {
  url: string
  params?: object
  signal?: AbortSignal
}

export interface V2RawRequestConfig extends V2RequestConfig {
  headers?: Record<string, string>
  responseType?: AxiosRequestConfig['responseType']
}

/** 解析统一 ApiResponse envelope；复用 legacy transport 的 refresh 锁与 auth replay。 */
export async function requestV2<T>(config: V2RequestConfig): Promise<T> {
  return request<T>({
    method: 'GET',
    url: config.url,
    params: config.params,
    signal: config.signal,
  }, { showError: false, retry: true })
}

/** Artifact Range 等非 envelope 读响应，仍经过同一 auth/refresh transport。 */
export async function requestRawV2<T>(config: V2RawRequestConfig): Promise<AxiosResponse<T>> {
  const axiosConfig: AxiosRequestConfig = {
    method: 'GET',
    url: config.url,
    params: config.params,
    signal: config.signal,
    headers: config.headers,
    responseType: config.responseType,
  }
  const first = await dispatch<T>(axiosConfig, true)
  const firstEnvelope = parseArrayBufferEnvelope(first)
  if (firstEnvelope?.code === 401) {
    const token = await refreshOrFail()
    const replay = await dispatch<T>(withAuthorization(axiosConfig, token), false)
    throwIfRawEnvelopeError(replay)
    return replay
  }
  throwIfRawEnvelopeError(first)
  return first
}

function parseArrayBufferEnvelope<T>(response: AxiosResponse<T>): ApiResponse<unknown> | null {
  // Successful artifact content is always 206. Base errors are HTTP-200 JSON envelopes,
  // which Axios exposes as ArrayBuffer because this request uses responseType=arraybuffer.
  if (response.status !== 200 || !(response.data instanceof ArrayBuffer)) return null
  try {
    const parsed = JSON.parse(new TextDecoder().decode(response.data)) as Partial<ApiResponse<unknown>>
    if (typeof parsed.code !== 'number' || typeof parsed.msg !== 'string') return null
    return parsed as ApiResponse<unknown>
  } catch {
    return null
  }
}

function throwIfRawEnvelopeError<T>(response: AxiosResponse<T>): void {
  const envelope = parseArrayBufferEnvelope(response)
  if (envelope && envelope.code !== 0) {
    throw new ApiRequestError(envelope.msg || '操作失败', envelope.code, response.status)
  }
}
