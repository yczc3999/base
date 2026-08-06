<template>
  <div>
    <!-- 工具栏 -->
    <div class="backup-toolbar">
      <el-button type="primary" :icon="DownloadIcon" :loading="backingUp" @click="manualBackup">
        {{ backingUp ? '备份中...' : '立即备份' }}
      </el-button>
      <span class="tip">
        保留策略：最近 7 天每日 + 最近 4 周每周，其余自动清理 · 恢复功能暂未提供
      </span>
    </div>

    <CrudTable ref="crudRef" api="admin/db_backup" perms="admin:db_backup"
      :columns="columns" :search-fields="searchFields" :action-width="160"
      :has-create="false" :has-edit="false">
      <template #actions="{ row }">
        <el-button type="primary" link size="small" :icon="DownloadIcon"
          @click="downloadBackup(row)">下载</el-button>
        <el-button type="danger" link size="small" :icon="DeleteIcon"
          @click="crudRef?.crud.handleDelete(row)">删除</el-button>
      </template>
    </CrudTable>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Download as DownloadIcon, Delete as DeleteIcon } from '@element-plus/icons-vue'
import CrudTable from '@/components/CrudTable/index.vue'
import type { CrudColumn, SearchField } from '@/components/CrudTable/types'
import { post } from '@/api/request'

const crudRef = ref()
const backingUp = ref(false)

const columns: CrudColumn[] = [
  { field: 'id', label: 'ID', width: 60 },
  { field: 'filename', label: '文件名', minWidth: 220 },
  { field: 'file_size', label: '大小', width: 110, formatter: (row: any, v: any) => formatSize(v) },
  { field: 'status', label: '状态', width: 90, align: 'center', type: 'status',
    statusMap: {
      ok: { label: '成功', type: 'success' },
      failed: { label: '失败', type: 'danger' },
    } },
  { field: 'started_at', label: '开始时间', width: 160, type: 'time' },
  { field: 'finished_at', label: '完成时间', width: 160, type: 'time' },
]

const searchFields: SearchField[] = [
  { field: 'keyword', label: '搜索', type: 'input', placeholder: '文件名' },
  { field: 'status', label: '状态', type: 'select', options: [
    { label: '成功', value: 'ok' }, { label: '失败', value: 'failed' },
  ] },
]

function formatSize(bytes: number) {
  if (!bytes && bytes !== 0) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

async function manualBackup() {
  backingUp.value = true
  try {
    const res = await post('/admin/db_backup/backup')
    ElMessage.success(`备份成功：${res?.filename}（${formatSize(res?.file_size)}）`)
    crudRef.value?.crud.getList()
  } catch {} finally { backingUp.value = false }
}

async function downloadBackup(row: any) {
  // 用 axios 二进制下载（带鉴权头）
  const { default: axios } = await import('axios')
  const { getToken } = await import('@/utils/auth')
  const BASE = import.meta.env.VITE_API_BASE_URL || ''
  const PREFIX = import.meta.env.VITE_API_PREFIX || '/api'
  try {
    const resp = await axios.get(`${BASE}${PREFIX}/admin/db_backup/download`, {
      params: { filename: row.filename },
      headers: { Authorization: `Bearer ${getToken()}` },
      responseType: 'blob',
    })
    const url = URL.createObjectURL(resp.data)
    const a = document.createElement('a')
    a.href = url
    a.download = row.filename
    a.click()
    URL.revokeObjectURL(url)
  } catch {
    ElMessage.error('下载失败')
  }
}
</script>

<style scoped>
.backup-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}
.tip {
  font-size: 12px;
  color: var(--text-secondary);
}
</style>
