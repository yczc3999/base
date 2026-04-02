/** 表格列配置 */
export interface CrudColumn {
  /** 字段名 */
  field: string
  /** 显示名 */
  label: string
  /** 列宽 */
  width?: number | string
  /** 最小宽度 */
  minWidth?: number | string
  /** 对齐 */
  align?: 'left' | 'center' | 'right'
  /** 是否可排序 */
  sortable?: boolean | 'custom'
  /** 渲染类型 */
  type?: 'text' | 'tag' | 'status' | 'time' | 'image'
  /** 状态映射（type=status 时） */
  statusMap?: Record<string | number, { label: string; type: string }>
  /** tag 映射（type=tag 时） */
  tagMap?: Record<string | number, { label: string; type: string }>
  /** 是否固定列 */
  fixed?: 'left' | 'right'
  /** 自定义格式化 */
  formatter?: (row: any, value: any) => string
}

/** 搜索字段配置 */
export interface SearchField {
  /** 字段名 */
  field: string
  /** 显示名 */
  label: string
  /** 类型 */
  type: 'input' | 'select' | 'dateRange' | 'date'
  /** placeholder */
  placeholder?: string
  /** 选项（select 类型） */
  options?: Array<{ label: string; value: any }>
}

/** 表单字段配置 */
export interface FormField {
  /** 字段名 */
  field: string
  /** 显示名 */
  label: string
  /** 类型 */
  type?: 'input' | 'password' | 'textarea' | 'select' | 'switch' | 'number' | 'radio' | 'imageUpload' | 'treeSelect'
  /** 校验规则 */
  rules?: any[]
  /** 选项 */
  options?: Array<{ label: string; value: any }>
  /** placeholder */
  placeholder?: string
  /** 默认值 */
  default?: any
  /** 仅创建时显示 */
  showOnCreate?: boolean
  /** 仅编辑时显示 */
  showOnEdit?: boolean
  /** 列宽占比（1=整行，0.5=半行） */
  span?: number
}
