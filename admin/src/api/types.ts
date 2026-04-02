/** 后端统一响应格式 */
export interface ApiResponse<T = any> {
  code: number
  msg: string
  data: T
}

/** 列表响应 */
export interface ListData<T = any> {
  list: T[]
  total: number
  page: number
  pageSize: number
}

/** 请求配置 */
export interface RequestOptions {
  /** 是否显示全屏 loading（默认 false） */
  loading?: boolean
  /** 是否弹出错误提示（默认 true） */
  showError?: boolean
  /** 401 时是否尝试 refresh（默认 true） */
  retry?: boolean
}
