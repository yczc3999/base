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
  type?: 'text' | 'tag' | 'status' | 'time' | 'image' | 'switch'
  /** 状态映射（type=status 时） */
  statusMap?: Record<string | number, { label: string; type: string }>
  /** tag 映射（type=tag 时） */
  tagMap?: Record<string | number, { label: string; type: 'primary' | 'success' | 'warning' | 'info' | 'danger' }>
  /** 是否固定列 */
  fixed?: 'left' | 'right'
  /** 自定义格式化 */
  formatter?: (row: any, value: any) => string
  /** switch 列的激活值 */
  activeValue?: any
  /** switch 列的非激活值 */
  inactiveValue?: any
}

/** 搜索字段配置 */
export interface SelectFieldOptions {
  /** 是否允许选择多个值（仅 type=select 生效） */
  multiple?: boolean
  /** 是否允许输入筛选选项；多选默认开启 */
  filterable?: boolean
  /** 是否将已选项折叠为标签；多选默认开启 */
  collapseTags?: boolean
  /** 鼠标悬停折叠标签时显示完整列表 */
  collapseTagsTooltip?: boolean
  /** 折叠标签时最多直接显示的数量 */
  maxCollapseTags?: number
  /** 是否显示清空按钮 */
  clearable?: boolean
}

export interface SearchField extends SelectFieldOptions {
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
export interface FormField extends SelectFieldOptions {
  /** 字段名 */
  field: string
  /** 显示名 */
  label: string
  /** 类型 */
  type?: 'input' | 'password' | 'textarea' | 'select' | 'switch' | 'number' | 'radio' | 'imageUpload' | 'treeSelect' | 'json' | 'date' | 'dateTime' | 'color' | 'editor'
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
  /** 禁用（只读展示） */
  disabled?: boolean
  /** 输入框后缀文字 */
  suffix?: string
}
