<template>
  <div>
    <!-- 工具栏: 模块选择 + 刷新 -->
    <div class="trash-toolbar">
      <el-select v-model="module" placeholder="选择模块" style="width: 200px" @change="loadList">
        <el-option v-for="m in modules" :key="m.module" :label="m.label" :value="m.module" />
      </el-select>
      <el-button :icon="Refresh" :loading="loading" @click="loadList">刷新</el-button>
      <template v-if="selection.length">
        <el-button
type="primary" :icon="RefreshLeft" :disabled="!hasPerms('admin:trash:restore')"
          @click="batchRestore">恢复选中 ({{ selection.length }})</el-button>
        <el-button
type="danger" :icon="Delete" :disabled="!hasPerms('admin:trash:purge')"
          @click="batchPurge">彻底删除 ({{ selection.length }})</el-button>
      </template>
    </div>

    <el-table v-loading="loading" :data="list" stripe :empty-text="'回收站为空'" @selection-change="selection = $event">
      <el-table-column type="selection" width="45" />
      <el-table-column prop="id" label="ID" width="70" align="center" />
      <el-table-column label="记录内容" min-width="260">
        <template #default="{ row }">{{ summarize(row) }}</template>
      </el-table-column>
      <el-table-column label="删除时间" width="170">
        <template #default="{ row }">{{ formatTime(row.deleted_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="160" align="center" fixed="right">
        <template #default="{ row }">
          <el-button
type="primary" link size="small" :icon="RefreshLeft"
            :disabled="!hasPerms('admin:trash:restore')" @click="restoreRow(row)">恢复</el-button>
          <el-button
type="danger" link size="small" :icon="Delete"
            :disabled="!hasPerms('admin:trash:purge')" @click="purgeRow(row)">彻底删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <div class="trash-pagination">
      <el-pagination
        v-model:current-page="page" v-model:page-size="pageSize"
        :total="total" :page-sizes="[10, 20, 50]" layout="total, sizes, prev, pager, next"
        @current-change="loadList" @size-change="loadList"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
defineOptions({ name: 'Trash' })
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index'
import { confirmDialog } from '@/utils/confirm'
import { Refresh, RefreshLeft, Delete } from '@element-plus/icons-vue'
import { get, post } from '@/api/request'
import { usePermission } from '@/hooks/usePermission'

const { hasPerms } = usePermission()

const modules = ref<any[]>([])
const module = ref('')
const list = ref<any[]>([])
const selection = ref<any[]>([])
const loading = ref(false)
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)

function formatTime(v: string) {
  if (!v) return '—'
  return v.replace('T', ' ').substring(0, 19)
}

function summarize(row: any) {
  // 通用展示: 过滤 id/时间戳/软删标记, 取前几个字段
  const skip = new Set(['id', 'created_at', 'updated_at', 'deleted_at'])
  const fields = Object.entries(row)
    .filter(([k, v]) => !skip.has(k) && v !== null && v !== '')
    .slice(0, 5)
    .map(([k, v]) => `${k}: ${v}`)
  return fields.join(' · ') || `#${row.id}`
}

async function loadModules() {
  try {
    modules.value = await get('/admin/trash/modules') || []
  } catch { /* 模块列表加载失败静默 */ }
}

async function loadList() {
  if (!module.value) return
  loading.value = true
  try {
    const data = await get('/admin/trash/list', {
      module: module.value, page: page.value, pageSize: pageSize.value,
    })
    list.value = data?.list || []
    total.value = data?.total || 0
  } catch { /* 列表加载失败静默 */ } finally { loading.value = false }
}

async function restoreRow(row: any) {
  await confirmDialog(`确认恢复记录 #${row.id}？`, '恢复', { type: 'warning' })
  await post('/admin/trash/restore', { module: module.value, ids: [row.id] })
  ElMessage.success('已恢复')
  loadList()
}

async function purgeRow(row: any) {
  await confirmDialog(
    `确认彻底删除记录 #${row.id}？此操作不可恢复！`, '彻底删除', { type: 'error' },
  )
  await post('/admin/trash/purge', { module: module.value, ids: [row.id] })
  ElMessage.success('已彻底删除')
  loadList()
}

async function batchRestore() {
  const ids = selection.value.map(r => r.id)
  await confirmDialog(`确认恢复 ${ids.length} 条记录？`, '批量恢复', { type: 'warning' })
  await post('/admin/trash/restore', { module: module.value, ids })
  ElMessage.success('已恢复')
  loadList()
}

async function batchPurge() {
  const ids = selection.value.map(r => r.id)
  await confirmDialog(`确认彻底删除 ${ids.length} 条记录？此操作不可恢复！`, '批量彻底删除', { type: 'error' })
  await post('/admin/trash/purge', { module: module.value, ids })
  ElMessage.success('已彻底删除')
  loadList()
}

onMounted(async () => {
  await loadModules()
  if (modules.value.length) {
    module.value = modules.value[0].module
    loadList()
  }
})
</script>

<style scoped>
.trash-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}
.trash-pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
</style>
