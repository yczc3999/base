<template>
  <CrudTable ref="crudRef" api="admin/search_keyword" perms="admin:search_keyword"
    :columns="columns" :search-fields="searchFields" :hasCreate="false" :hasDelete="false">

    <template #toolbar>
      <el-button type="success" :icon="Check" :disabled="!crudRef?.crud?.selections?.value?.length"
        @click="bulkApprove">批量上线</el-button>
      <el-button type="info" :icon="Close" :disabled="!crudRef?.crud?.selections?.value?.length"
        @click="bulkReject">批量忽略</el-button>
      <el-divider direction="vertical" />
      <el-button type="warning" :icon="Refresh" :loading="polling" @click="pollHarvest">
        {{ polling ? '采集中...' : '轮询采集' }}
      </el-button>
      <el-button :icon="Download" @click="openHarvestDialog">手动采集</el-button>
      <el-button :icon="MagicStick" :loading="aiLoading" @click="getAiSeeds">AI 种子词</el-button>
    </template>
  </CrudTable>

  <!-- 采集弹窗（手动 + 轮询共用进度面板） -->
  <el-dialog v-model="showHarvest" title="采集关键词" width="620px" :close-on-click-modal="false">
    <el-form v-if="harvestMode === 'manual'" label-width="80px">
      <el-form-item label="种子词">
        <el-input v-model="harvestForm.seedsText" type="textarea" :rows="6"
          placeholder="每行一个种子关键词，如：&#10;海外看B站&#10;回国VPN推荐&#10;watch bilibili abroad" />
        <div class="form-hint">当前 {{ seedCount }} 个（上限 50）</div>
      </el-form-item>
      <el-form-item label="搜索引擎">
        <el-checkbox-group v-model="harvestForm.engines">
          <el-checkbox label="google">Google</el-checkbox>
          <el-checkbox label="duckduckgo">DuckDuckGo</el-checkbox>
          <el-checkbox label="yandex">Yandex</el-checkbox>
        </el-checkbox-group>
      </el-form-item>
    </el-form>
    <div v-else class="harvest-tip">
      <span>🔄 从库里取未采集过的关键词继续扩展（每次 {{ 20 }} 个种子，上限 100 个新词）</span>
    </div>

    <!-- 进度日志 -->
    <div v-if="harvestLog.length" class="harvest-log">
      <div class="log-header">
        <span>采集进度</span>
        <el-tag v-if="harvestDone" type="success" size="small">完成</el-tag>
        <el-tag v-else type="warning" size="small" effect="plain">采集中... {{ harvestTotal }} 个</el-tag>
      </div>
      <div class="log-body">
        <div v-for="(log, i) in harvestLog" :key="i" class="log-line" :class="log.type">
          <template v-if="log.type === 'level'">📂 第 {{ log.level + 1 }} 层，{{ log.seeds }} 个种子</template>
          <template v-else-if="log.type === 'seed'">🔍 {{ log.seed }}</template>
          <template v-else-if="log.type === 'found'">
            ✅ [{{ log.engine }}] {{ log.seed }} → <strong>+{{ log.keywords.length }}</strong>
            <span class="kw-list">{{ log.keywords.join('、') }}</span>
          </template>
          <template v-else-if="log.type === 'done'">
            🏁 采集完成：{{ log.total ?? 0 }} 个，入库 {{ log.imported ?? 0 }} 个
            <template v-if="log.msg"> — {{ log.msg }}</template>
          </template>
        </div>
      </div>
    </div>

    <template #footer>
      <el-button @click="showHarvest = false" :disabled="harvesting || polling">关闭</el-button>
      <el-button v-if="harvestMode === 'manual'" type="primary"
        :loading="harvesting" :disabled="seedCount === 0 || harvesting"
        @click="doHarvest">
        {{ harvesting ? `采集中 (${harvestTotal})...` : `开始采集 (${seedCount} 个种子)` }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Check, Close, Download, MagicStick, Refresh } from '@element-plus/icons-vue'
import CrudTable from '@/components/CrudTable/index.vue'
import type { CrudColumn, SearchField } from '@/components/CrudTable/types'
import { post } from '@/api/request'

const crudRef = ref()

const columns: CrudColumn[] = [
  { field: 'id', label: 'ID', width: 60 },
  { field: 'keyword', label: '关键词', minWidth: 200 },
  { field: 'source', label: '来源', width: 100, type: 'tag',
    tagMap: {
      0: { label: '手动', type: 'info' },
      1: { label: 'Google', type: 'primary' },
      2: { label: 'Yandex', type: 'warning' },
      3: { label: 'DuckDuckGo', type: 'success' },
    } },
  { field: 'seed_keyword', label: '种子词', width: 150 },
  { field: 'status', label: '审核', width: 90, align: 'center', type: 'status',
    statusMap: {
      0: { label: '待审核', type: 'warning' },
      1: { label: '已上线', type: 'success' },
      2: { label: '已忽略', type: 'info' },
    } },
  { field: 'harvested', label: '已采集', width: 80, align: 'center', type: 'status',
    statusMap: {
      true: { label: '已采集', type: 'success' },
      false: { label: '待采集', type: 'danger' },
    } },
  { field: 'tag_id', label: '标签ID', width: 80 },
  { field: 'fetched_at', label: '采集时间', width: 170, type: 'time' },
]

const searchFields: SearchField[] = [
  { field: 'keyword', label: '搜索', placeholder: '关键词' },
  { field: 'status', label: '状态', type: 'select', options: [
    { label: '待审核', value: 0 }, { label: '已上线', value: 1 }, { label: '已忽略', value: 2 },
  ] },
  { field: 'source', label: '来源', type: 'select', options: [
    { label: '手动', value: 0 }, { label: 'Google', value: 1 },
    { label: 'Yandex', value: 2 }, { label: 'DuckDuckGo', value: 3 },
  ] },
]

// ---- 批量操作 ----

async function bulkApprove() {
  const ids = crudRef.value?.crud?.selections?.value?.map((r: any) => r.id) || []
  if (!ids.length) return
  await ElMessageBox.confirm(`确认上线 ${ids.length} 个关键词？上线后会自动创建对应标签。`, '批量上线')
  const res = await post('/admin/search_keyword/bulk-approve', { ids })
  ElMessage.success(`已上线 ${res?.approved ?? 0} 个，创建 ${res?.tags_created ?? 0} 个标签`)
  crudRef.value?.crud?.getList()
}

async function bulkReject() {
  const ids = crudRef.value?.crud?.selections?.value?.map((r: any) => r.id) || []
  if (!ids.length) return
  await ElMessageBox.confirm(`确认忽略 ${ids.length} 个关键词？`, '批量忽略')
  const res = await post('/admin/search_keyword/bulk-reject', { ids })
  ElMessage.success(`已忽略 ${res?.rejected ?? 0} 个`)
  crudRef.value?.crud?.getList()
}

// ---- 采集（手动 + 轮询共用 SSE + 进度面板）----

const showHarvest = ref(false)
const harvestMode = ref<'manual' | 'poll'>('manual')
const harvesting = ref(false)
const polling = ref(false)
const harvestLog = ref<any[]>([])
const harvestDone = ref(false)
const harvestTotal = ref(0)
const harvestForm = ref({ seedsText: '', engines: ['google', 'duckduckgo'] as string[] })
const seedCount = computed(() =>
  harvestForm.value.seedsText.split('\n').map(s => s.trim()).filter(Boolean).length
)

function openHarvestDialog() {
  harvestMode.value = 'manual'
  resetHarvestState()
  showHarvest.value = true
}

function resetHarvestState() {
  harvestLog.value = []
  harvestDone.value = false
  harvestTotal.value = 0
}

async function pollHarvest() {
  await ElMessageBox.confirm(
    '从库里取未采集过的关键词继续扩展，每次 20 个种子，最多采集 100 个新词。',
    '轮询采集', { type: 'info' }
  )
  harvestMode.value = 'poll'
  resetHarvestState()
  showHarvest.value = true
  polling.value = true
  try {
    await startSSE('/api/admin/search_keyword/poll-harvest-stream',
      { batch_size: 20, engines: ['google', 'duckduckgo'], max_total: 100 })
  } finally {
    polling.value = false
    crudRef.value?.crud?.getList()
  }
}

async function doHarvest() {
  const seeds = harvestForm.value.seedsText.split('\n').map(s => s.trim()).filter(Boolean)
  if (!seeds.length) return ElMessage.warning('请输入种子关键词')
  if (seeds.length > 50) return ElMessage.warning('单次最多 50 个种子词')
  if (!harvestForm.value.engines.length) return ElMessage.warning('请选择至少一个搜索引擎')

  harvesting.value = true
  resetHarvestState()
  try {
    await startSSE('/api/admin/search_keyword/harvest-stream',
      { seeds, engines: harvestForm.value.engines })
  } finally {
    harvesting.value = false
    crudRef.value?.crud?.getList()
  }
}

async function startSSE(url: string, body: any) {
  const token = localStorage.getItem('access_token') || ''
  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
    body: JSON.stringify(body),
  })
  if (!resp.ok || !resp.body) {
    ElMessage.error('采集请求失败')
    return
  }
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''
    for (const line of lines) {
      if (!line.startsWith('data: ')) continue
      try {
        const evt = JSON.parse(line.slice(6))
        harvestLog.value.push(evt)
        if (evt.total !== undefined) harvestTotal.value = evt.total
        if (evt.type === 'done') {
          harvestDone.value = true
          const imp = evt.imported ?? 0
          ElMessage.success(`采集完成：${imp} 个新词入库`)
        }
      } catch { /* skip malformed line */ }
    }
  }
}

// ---- AI 种子词 ----

const aiLoading = ref(false)

async function getAiSeeds() {
  aiLoading.value = true
  try {
    const res = await post('/admin/search_keyword/ai-seeds', {
      topic: '海外华人回国加速 VPN 看视频',
      count: 20,
    })
    const seeds = res?.seeds || []
    harvestForm.value.seedsText = seeds.join('\n')
    harvestMode.value = 'manual'
    resetHarvestState()
    showHarvest.value = true
    ElMessage.success(`AI 推荐了 ${seeds.length} 个种子词，可编辑后开始采集`)
  } finally {
    aiLoading.value = false
  }
}
</script>

<style scoped>
.form-hint { font-size: 12px; color: var(--el-text-color-secondary); margin-top: 4px; }
.harvest-tip { padding: 10px 14px; background: var(--el-color-info-light-9); border-radius: 4px;
  font-size: 13px; color: var(--el-text-color-regular); margin-bottom: 12px; }
.harvest-log { margin-top: 16px; border: 1px solid var(--el-border-color-lighter); border-radius: 4px; }
.log-header { padding: 8px 12px; background: var(--el-fill-color-light);
  display: flex; justify-content: space-between; align-items: center; font-size: 13px; }
.log-body { max-height: 260px; overflow-y: auto; padding: 8px 12px; font-size: 12px; line-height: 1.8; }
.log-line { border-bottom: 1px dashed var(--el-border-color-lighter); padding: 2px 0; }
.log-line:last-child { border-bottom: none; }
.log-line.done { color: var(--el-color-success); font-weight: 500; }
.kw-list { color: var(--el-text-color-secondary); margin-left: 6px; }
</style>
