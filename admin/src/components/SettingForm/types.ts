/** 配置项 provider 定义 */
export interface SettingProvider {
  /** 标识（对应 settings 表的 name，如 'aliyun'） */
  key: string
  /** 显示名 */
  label: string
  /** 图标/logo（可选） */
  icon?: string
  /** 描述 */
  desc?: string
  /** 表单字段 */
  fields: SettingField[]
}

/** 配置表单字段 */
export interface SettingField {
  /** 字段名 */
  name: string
  /** 显示名 */
  label: string
  /** 类型 */
  type?: 'input' | 'password' | 'textarea' | 'number' | 'switch' | 'select'
  /** placeholder */
  placeholder?: string
  /** 是否必填 */
  required?: boolean
  /** 提示信息 */
  tip?: string
  /** select 选项 */
  options?: Array<{ label: string; value: any }>
  /** 默认值 */
  default?: any
}

/** 附加配置（不属于任何 provider 的全局配置） */
export interface SettingExtra {
  /** 字段名 */
  name: string
  /** 显示名 */
  label: string
  /** 类型 */
  type?: 'input' | 'number' | 'switch' | 'select'
  /** 提示 */
  tip?: string
  /** select 选项 */
  options?: Array<{ label: string; value: any }>
  /** 默认值 */
  default?: any
}
