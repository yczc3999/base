import type { CrudColumn, SearchField, FormField } from '@/components/CrudTable/types'

/**
 * F5 — Schema 驱动的 CRUD 页类型。
 *
 * 「一页一 JSON」：一个页面只需导出一份 CrudPageSchema，SchemaCrudPage
 * 组件据此渲染 PageShell + StatCard(s) + CrudTable。复杂的业务页仍可
 * 保留自定义模板，schema 只覆盖标准 CRUD 场景。
 */
export interface CrudStatCard {
  /** 统计字段（取自列表数据或接口） */
  key: string
  /** 图标（lucide / element-plus 图标组件） */
  icon?: any
  /** 显示名 */
  label: string
  /** 强调色（CSS 色值，如 'var(--primary)'） */
  accent: string
}

export interface CrudBatchAction {
  key: string
  label: string
  danger?: boolean
}

export interface CrudPageSchema {
  /** 页面标题（PageShell title） */
  title: string
  /** 副标题（可选） */
  subTitle?: string
  /** 顶部统计卡（可选） */
  statCards?: CrudStatCard[]
  /** 搜索字段（可选） */
  filters?: SearchField[]
  /** 表格列 */
  columns: CrudColumn[]
  /** 表单字段（可选，缺省则不可新增/编辑） */
  formFields?: FormField[]
  /** 批量操作（可选） */
  batchActions?: CrudBatchAction[]
  /** 是否支持导出 */
  exportable?: boolean
  /** 是否支持导入（可选） */
  importable?: boolean
  /** 导入逻辑模块名 */
  importModule?: string
  /** 是否显示新增按钮（默认 true，缺省则按 CrudTable 默认） */
  hasCreate?: boolean
  /** 是否显示编辑按钮（默认 true，缺省则按 CrudTable 默认） */
  hasEdit?: boolean
  /** 是否显示删除按钮（默认 true，缺省则按 CrudTable 默认） */
  hasDelete?: boolean
  /** 操作列宽度（缺省则按 CrudTable 默认 180） */
  actionWidth?: number
  /** 是否显示 keyword 搜索框（默认 true） */
  showKeyword?: boolean
  /** 编辑弹窗宽度（缺省则按 CrudTable 默认 '560px'） */
  dialogWidth?: string | number
  /** CrudTable api 路径，如 'admin/role' */
  api: string
  /** 权限前缀，如 'admin:role' */
  perms: string
}
