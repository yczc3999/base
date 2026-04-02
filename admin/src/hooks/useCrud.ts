/**
 * useCrud — 通用 CRUD 逻辑 Hook
 *
 * 封装列表查询、搜索、分页、排序、新增、编辑、删除的全部逻辑。
 * 页面只需传入 API 路径，即可获得完整 CRUD 能力。
 *
 * 用法：
 *   const crud = useCrud({ api: 'admin/user' })
 *   // crud.tableData / crud.loading / crud.getList() / crud.handleAdd() / ...
 */

import { ref, reactive, onMounted } from 'vue'
import { createCrudApi, type CrudApi } from '@/api/crud'
import { ElMessage, ElMessageBox } from 'element-plus'
import NProgress from '@/utils/nprogress'

export interface CrudOptions {
  /** API 路径前缀（如 'admin/user'），自动生成 CRUD 接口 */
  api: string | CrudApi
  /** 默认查询参数 */
  defaultQuery?: Record<string, any>
  /** 是否立即加载 */
  immediate?: boolean
  /** 每页条数 */
  pageSize?: number
  /** 删除前确认文案 */
  deleteConfirm?: string
}

export function useCrud(options: CrudOptions) {
  const {
    defaultQuery = {},
    immediate = true,
    pageSize = 20,
    deleteConfirm = '确定要删除吗？删除后无法恢复。',
  } = options

  // API
  const api: CrudApi = typeof options.api === 'string'
    ? createCrudApi(options.api)
    : options.api

  // ── 状态 ──
  const loading = ref(false)
  const tableData = ref<any[]>([])
  const total = ref(0)
  const selections = ref<any[]>([])

  const queryParams = reactive({
    page: 1,
    pageSize,
    sortField: 'id',
    sortOrder: 'desc',
    keyword: '',
    filters: {} as Record<string, any>,
    ...defaultQuery,
  })

  // 表单
  const formVisible = ref(false)
  const formMode = ref<'create' | 'edit'>('create')
  const formData = ref<Record<string, any>>({})
  const formLoading = ref(false)

  // 详情
  const detailVisible = ref(false)
  const detailData = ref<Record<string, any>>({})

  // ── 列表 ──
  async function getList() {
    loading.value = true
    NProgress.start()
    try {
      const params: any = {
        page: queryParams.page,
        pageSize: queryParams.pageSize,
        sortField: queryParams.sortField,
        sortOrder: queryParams.sortOrder,
      }
      if (queryParams.keyword) params.keyword = queryParams.keyword
      if (Object.keys(queryParams.filters).length) {
        params.filters = JSON.stringify(queryParams.filters)
      }
      const data = await api.getList(params)
      tableData.value = data.list || []
      total.value = data.total || 0
    } catch {}
    loading.value = false
    NProgress.done()
  }

  // ── 搜索 ──
  function handleSearch() {
    queryParams.page = 1
    getList()
  }

  function handleReset() {
    queryParams.page = 1
    queryParams.keyword = ''
    queryParams.filters = {}
    getList()
  }

  // ── 分页 ──
  function handlePageChange(page: number) {
    queryParams.page = page
    getList()
  }

  function handleSizeChange(size: number) {
    queryParams.pageSize = size
    queryParams.page = 1
    getList()
  }

  // ── 排序 ──
  function handleSortChange({ prop, order }: any) {
    queryParams.sortField = prop || 'id'
    queryParams.sortOrder = order === 'ascending' ? 'asc' : 'desc'
    getList()
  }

  // ── 选择 ──
  function handleSelectionChange(rows: any[]) {
    selections.value = rows
  }

  // ── 新增 ──
  function handleAdd() {
    formMode.value = 'create'
    formData.value = {}
    formVisible.value = true
  }

  // ── 编辑 ──
  async function handleEdit(row: any) {
    formMode.value = 'edit'
    try {
      const data = await api.getDetail(row.id)
      formData.value = { ...data }
    } catch {
      formData.value = { ...row }
    }
    formVisible.value = true
  }

  // ── 详情 ──
  async function handleDetail(row: any) {
    try {
      detailData.value = await api.getDetail(row.id)
    } catch {
      detailData.value = { ...row }
    }
    detailVisible.value = true
  }

  // ── 提交表单 ──
  async function handleSubmit(data?: Record<string, any>) {
    formLoading.value = true
    try {
      await api.doEdit(data || formData.value)
      ElMessage.success(formMode.value === 'create' ? '创建成功' : '更新成功')
      formVisible.value = false
      getList()
    } catch {}
    formLoading.value = false
  }

  // ── 删除 ──
  async function handleDelete(row: any) {
    try {
      await ElMessageBox.confirm(deleteConfirm, '提示', {
        type: 'warning',
        confirmButtonText: '确定',
        cancelButtonText: '取消',
      })
      await api.doDelete(row.id)
      ElMessage.success('删除成功')
      getList()
    } catch {}
  }

  // ── 批量删除 ──
  async function handleBatchDelete() {
    if (!selections.value.length) {
      ElMessage.warning('请先选择数据')
      return
    }
    try {
      await ElMessageBox.confirm(`确定要删除选中的 ${selections.value.length} 条数据吗？`, '提示', {
        type: 'warning',
      })
      const ids = selections.value.map((r) => r.id)
      await api.doDelete(ids)
      ElMessage.success('删除成功')
      getList()
    } catch {}
  }

  // ── 初始化 ──
  if (immediate) {
    onMounted(getList)
  }

  return {
    // 状态
    loading,
    tableData,
    total,
    queryParams,
    selections,
    formVisible,
    formMode,
    formData,
    formLoading,
    detailVisible,
    detailData,

    // 方法
    api,
    getList,
    handleSearch,
    handleReset,
    handlePageChange,
    handleSizeChange,
    handleSortChange,
    handleSelectionChange,
    handleAdd,
    handleEdit,
    handleDetail,
    handleSubmit,
    handleDelete,
    handleBatchDelete,
  }
}
