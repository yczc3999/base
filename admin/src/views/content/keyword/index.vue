<template>
  <div>
    <!-- 统计卡片 -->
    <div v-if="statsData" class="stat-cards">
      <div
class="stat-card pending" role="button" tabindex="0"
        @click="filterByStage('candidate')"
        @keydown.enter.prevent="filterByStage('candidate')"
        @keydown.space.prevent="filterByStage('candidate')">
        <div class="stat-num">{{ statsData.candidate }}</div>
        <div class="stat-label">待审核</div>
      </div>
      <div
class="stat-card online" role="button" tabindex="0"
        @click="filterByStage('approved')"
        @keydown.enter.prevent="filterByStage('approved')"
        @keydown.space.prevent="filterByStage('approved')">
        <div class="stat-num">{{ statsData.approved }}</div>
        <div class="stat-label">已上线</div>
      </div>
      <div
class="stat-card ignored" role="button" tabindex="0"
        @click="filterByStage('archived')"
        @keydown.enter.prevent="filterByStage('archived')"
        @keydown.space.prevent="filterByStage('archived')">
        <div class="stat-num">{{ statsData.archived }}</div>
        <div class="stat-label">已忽略</div>
      </div>
      <div class="stat-card harvest">
        <div class="stat-num">{{ statsData.unharvested }}</div>
        <div class="stat-label">待采集</div>
      </div>
      <div class="stat-card total">
        <div class="stat-num">{{ statsData.total }}</div>
        <div class="stat-label">总计</div>
      </div>
    </div>

    <CrudTable
ref="crudRef" api="admin/keyword" perms="admin:keyword"
      :columns="columns" :search-fields="searchFields" :form-fields="formFields">

      <template #toolbar>
        <el-button
type="success" :icon="Check" :disabled="!hasSelection"
          @click="bulkApprove">上线 ({{ selectionCount }})</el-button>
        <el-button
:icon="Close" :disabled="!hasSelection"
          @click="bulkReject">忽略 ({{ selectionCount }})</el-button>
        <el-button
type="danger" plain :icon="Remove" :disabled="!hasSelection"
          @click="bulkOffline">下线 ({{ selectionCount }})</el-button>
        <el-divider direction="vertical" />
        <el-button type="warning" :icon="Refresh" :loading="polling" @click="pollHarvest">
          {{ polling ? '采集中...' : '轮询采集' }}
        </el-button>
        <el-button :icon="Download" @click="openManualHarvest">手动采集</el-button>
        <el-button :icon="MagicStick" :loading="aiLoading" @click="getAiSeeds">AI 种子词</el-button>
        <el-divider direction="vertical" />
        <el-button :icon="Setting" @click="openPromptDialog">审核规则</el-button>
        <el-dropdown trigger="click" :disabled="aiReviewing" @command="onReviewCommand">
          <el-button type="primary" :icon="Cpu" :loading="aiReviewing">
            {{ aiReviewing ? `AI 审核中 (${aiScopeLabel})...` : 'AI 审核' }}
            <el-icon v-if="!aiReviewing" class="el-icon--right"><ArrowDown /></el-icon>
          </el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="pending">只审待审核（status=0）</el-dropdown-item>
              <el-dropdown-item command="online">重审已上线（status=1）</el-dropdown-item>
              <el-dropdown-item command="all" divided>全部重审（pending + online）</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </template>
    </CrudTable>

    <!-- 采集弹窗 -->
    <el-dialog
v-model="showHarvest" :title="harvestMode === 'poll' ? '轮询采集' : harvestSource === 'ai' ? 'AI 种子词' : '手动采集'"
      width="620px" :close-on-click-modal="false">
      <!-- 手动：种子表单 -->
      <template v-if="harvestMode === 'manual'">
        <div class="harvest-tip">
          <span class="tip-icon">💡</span>
          <span>输入种子关键词，从搜索引擎建议中扩展相关词，自动入库为「待审核」。单次上限 200 个。</span>
        </div>
        <el-form label-width="80px">
          <el-form-item label="种子词">
            <el-input
v-model="harvestForm.seedsText" type="textarea" :rows="8"
              placeholder="每行一个种子关键词，如：&#10;海外看B站&#10;回国VPN推荐&#10;watch bilibili abroad&#10;&#10;建议 10-30 个" />
            <div class="form-hint">当前 {{ seedCount }} 个种子词（上限 50）</div>
          </el-form-item>
          <el-form-item label="搜索引擎">
            <el-checkbox-group v-model="harvestForm.engines">
              <el-checkbox label="google">Google</el-checkbox>
              <el-checkbox label="duckduckgo">DuckDuckGo</el-checkbox>
              <el-checkbox label="yandex">Yandex</el-checkbox>
            </el-checkbox-group>
          </el-form-item>
        </el-form>
      </template>

      <!-- 轮询：直接提示 -->
      <div v-else class="harvest-tip">
        <span class="tip-icon">🔄</span>
        <span>从库里未采集标签持续滚动采集（每轮 20 个种子 × 多搜索引擎，直到池空或上限 5000），无需反复点击。</span>
      </div>
      <!-- 采集进度（SSE 实时） -->
      <div v-if="harvestLog.length" class="harvest-log">
        <div class="log-header">
          <span>采集进度</span>
          <el-tag v-if="harvestDone" type="success" size="small">完成</el-tag>
          <el-tag v-else type="warning" size="small" effect="plain">
            <i class="el-icon-loading" /> 采集中... {{ harvestTotal }} 个
          </el-tag>
        </div>
        <div ref="logBody" class="log-body">
          <div v-for="(log, i) in harvestLog" :key="i" class="log-line" :class="log.type">
            <template v-if="log.type === 'level'">📂 第 {{ log.level + 1 }} 层，{{ log.seeds }} 个种子</template>
            <template v-else-if="log.type === 'round_start'">
              🔁 <strong>第 {{ log.round }} 轮</strong>，摇到 {{ log.seeds_count }} 个种子：
              <span class="kw-list">{{ log.seeds.join('、') }}</span>
            </template>
            <template v-else-if="log.type === 'round_done'">
              🏁 第 {{ log.round }} 轮完成：新增 {{ log.found }}，入库 {{ log.imported }}
              <span class="kw-list">（累计 {{ log.cumulative_imported }}/{{ log.cumulative_found }}）</span>
            </template>
            <template v-else-if="log.type === 'seed'">🔍 {{ log.seed }}</template>
            <template v-else-if="log.type === 'found'">
              ✅ [{{ log.engine }}] {{ log.seed }} → <strong>+{{ log.keywords.length }}</strong>
              <span class="kw-list">{{ log.keywords.join('、') }}</span>
            </template>
            <template v-else-if="log.type === 'done'">
              🏁 采集完成：{{ log.total }} 个，入库 {{ log.imported }} 个
            </template>
          </div>
        </div>
      </div>

      <template #footer>
        <el-button :disabled="harvesting || polling" @click="showHarvest = false">关闭</el-button>
        <!-- 手动采集：有种子才能开始 -->
        <el-button
v-if="harvestMode === 'manual'" type="primary"
          :loading="harvesting" :disabled="seedCount === 0 || harvesting"
          @click="doHarvest">
          {{ harvesting ? `采集中 (${harvestTotal})...` : `开始采集 (${seedCount} 个种子)` }}
        </el-button>
        <!-- 轮询完成后：继续下一批 -->
        <el-button
v-if="harvestMode === 'poll' && harvestDone && !polling" type="warning"
          @click="pollHarvest">
          继续下一批
        </el-button>
      </template>
    </el-dialog>

    <!-- AI 审核实时面板 -->
    <el-dialog v-model="showAiResult" :title="`AI 审核 — ${aiScopeLabel}`" width="750px" :close-on-click-modal="false">
      <!-- 汇总 -->
      <div v-if="aiSummary" class="ai-summary">
        <span class="approve">✅ 上线 {{ aiSummary.approved_total }}</span>
        <span class="reject">❌ 删除 {{ aiSummary.rejected_total }}</span>
        <span v-if="aiSummary.demoted_total" class="demoted">↩️ 降回 {{ aiSummary.demoted_total }}</span>
        <span class="uncertain">⏳ 保留 {{ aiSummary.uncertain_total }}</span>
      </div>
      <div v-else-if="aiReviewing" style="margin-bottom:12px;color:var(--el-color-warning);font-size:13px">
        ⏳ AI 正在逐批审核（{{ aiScopeLabel }}）...
      </div>

      <!-- 日志 -->
      <div ref="aiLogBody" class="ai-review-log">
        <template v-for="(evt, i) in aiLog" :key="i">
          <div v-if="evt.type === 'batch_start'" class="log-line level">
            📦 第 {{ Math.floor(i / 2) + 1 }} 批，{{ evt.batch_size }} 个
          </div>
          <div v-else-if="evt.type === 'batch_done'" class="log-line">
            <div v-for="r in evt.results" :key="r.keyword" class="review-item">
              <el-tag :type="r.decision === 'approve' ? 'success' : r.decision === 'reject' ? 'danger' : 'warning'" size="small">
                {{ r.decision === 'approve' ? '上线' : r.decision === 'reject' ? '删除' : '保留' }}
              </el-tag>
              <span class="kw-text">{{ r.keyword }}</span>
            </div>
          </div>
          <div v-else-if="evt.type === 'error'" class="log-line" style="color:var(--el-color-danger)">
            ⚠️ {{ evt.msg }}
          </div>
          <div v-else-if="evt.type === 'done'" class="log-line done">
            🏁 全部完成：上线 {{ evt.approved_total }}，删除 {{ evt.rejected_total }}，保留 {{ evt.uncertain_total }}
          </div>
        </template>
      </div>

      <template #footer>
        <el-button :disabled="aiReviewing" @click="showAiResult = false">关闭</el-button>
      </template>
    </el-dialog>

    <!-- 审核规则配置弹窗 -->
    <el-dialog v-model="showPromptDialog" title="🤖 AI 审核规则" width="780px" :close-on-click-modal="false">
      <div class="prompt-intro">
        AI 按照这套规则判断每个标签是 <b>approve / reject / uncertain</b>。<br>
        不同项目目标受众不同，建议自己写或让 AI 帮你生成。
      </div>

      <!-- AI 辅助生成 -->
      <div class="ai-assist">
        <div class="ai-assist-title">✨ AI 辅助：描述你的业务，AI 帮你写规则</div>
        <el-form label-width="80px" size="small">
          <el-form-item label="业务">
            <el-input
v-model="genForm.industry" type="textarea" :rows="2"
              placeholder="例：海外华人回国加速，提供 VPN 服务让海外用户访问 B 站、爱奇艺、腾讯视频等国内平台" />
          </el-form-item>
          <el-form-item label="目标用户">
            <el-input
v-model="genForm.audience"
              placeholder="例：海外华人 / 中国留学生 / 在海外的中国出差人员（可空）" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :icon="MagicStick" :loading="generating" @click="generatePrompt">
              {{ generating ? 'AI 思考中...' : '生成审核规则' }}
            </el-button>
          </el-form-item>
        </el-form>
      </div>

      <el-divider>或手动编辑</el-divider>

      <el-form label-width="80px" size="small">
        <el-form-item label="System">
          <el-input
v-model="reviewForm.tag_system_prompt" type="textarea" :rows="2"
            placeholder="AI 的身份和约束（短，1-2 行）。例：你是 SEO 关键词严审员，只返回 JSON。宁严勿宽。" />
        </el-form-item>
        <el-form-item label="审核规则">
          <el-input
v-model="reviewForm.tag_user_template" type="textarea" :rows="14"
            :placeholder="placeholderUser" />
          <div class="prompt-hint-line">
            必须含 <code>{{ '{keywords}' }}</code> 占位符（后端会把待审词列表注入这里）。
            结尾要求 AI 『只返回 JSON 数组 [{"keyword":"...","decision":"approve|reject|uncertain"}]』
          </div>
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button :icon="RefreshRight" @click="loadPromptDefaults">恢复内置默认</el-button>
        <el-button :icon="Delete" @click="clearPrompt">清空</el-button>
        <el-button @click="showPromptDialog = false">取消</el-button>
        <el-button type="primary" :icon="Check" @click="savePromptAndClose">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
defineOptions({ name: 'ContentKeyword' })
import { ref, reactive, computed, onMounted, nextTick } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index'
import { confirmDialog } from '@/utils/confirm'
import { Check, Close, Remove, Download, MagicStick, Refresh, Cpu, ArrowDown, RefreshRight, Delete, Setting } from '@element-plus/icons-vue'
import { createSettingApi } from '@/api/settings'
import CrudTable from '@/components/CrudTable/index.vue'
import type { CrudColumn, SearchField, FormField } from '@/components/CrudTable/types'
import { get, post } from '@/api/request'
import { useSSE } from '@/hooks/useSSE'

const crudRef = ref()

// AI 审核规则（弹窗）
const showPromptDialog = ref(false)
const reviewApi = createSettingApi('ai_review')
const reviewForm = reactive({ tag_system_prompt: '', tag_user_template: '' })
const genForm = reactive({ industry: '', audience: '' })
const generating = ref(false)

const placeholderUser = `例如：

你是 SEO 关键词审核员。业务是『XXX』。目标用户是『YYY』。

★ 关键判断：搜索者是不是我们的目标用户？

✅ approve（这些是目标用户在搜）：
  - 例 1
  - 例 2
  - 例 3

❌ reject（明确无关或反方向）：
  - 例 1
  - 例 2

⚠️ uncertain：只在真的无法判断时用，默认宁严勿宽。

待审关键词：
{keywords}

只返回 JSON 数组，每项 {"keyword":"...","decision":"approve|reject|uncertain"}。`

async function loadPrompt() {
  try {
    const data = await reviewApi.getAll()
    Object.assign(reviewForm, data)
  } catch { /* 加载审核规则失败则用内置默认 */ }
}

function openPromptDialog() {
  showPromptDialog.value = true
  loadPrompt()
}

async function savePromptAndClose() {
  const tpl = reviewForm.tag_user_template
  if (tpl && !tpl.includes('{keywords}')) {
    return ElMessage.warning('「审核规则」必须包含 {keywords} 占位符（或留空用默认）')
  }
  await reviewApi.setMany(reviewForm)
  ElMessage.success('已保存')
  showPromptDialog.value = false
}

async function loadPromptDefaults() {
  try {
    const d = await get('/admin/setting/ai-review/defaults')
    reviewForm.tag_system_prompt = d?.tag_system_prompt || ''
    reviewForm.tag_user_template = d?.tag_user_template || ''
    ElMessage.info('已填入内置默认，点"保存"生效')
  } catch {
    ElMessage.error('读取默认失败')
  }
}

async function clearPrompt() {
  await confirmDialog('清空后将使用系统内置提示词（适合归途项目，新业务可能不准）。继续？', '清空', { type: 'warning' })
  reviewForm.tag_system_prompt = ''
  reviewForm.tag_user_template = ''
  await reviewApi.setMany(reviewForm)
  ElMessage.success('已清空，下次审核将用内置默认')
}

async function generatePrompt() {
  if (!genForm.industry || genForm.industry.trim().length < 5) {
    return ElMessage.warning('请先描述业务（至少 5 个字）')
  }
  generating.value = true
  try {
    const res = await post('/admin/setting/ai-review/generate', genForm)
    reviewForm.tag_system_prompt = res?.system || ''
    reviewForm.tag_user_template = res?.user_template || ''
    ElMessage.success('AI 已生成，检查后点"保存"应用')
  } catch {
    ElMessage.error('AI 生成失败')
  } finally { generating.value = false }
}

// AI 审核入口拦截
async function onReviewCommand(scope: 'pending' | 'online' | 'all') {
  await loadPrompt()
  const hasCustom = (reviewForm.tag_user_template || '').trim().length > 0
  if (!hasCustom) {
    await confirmDialog(
      '当前审核规则为系统内置（适合归途项目）。\n如果你的业务不同，建议先点「审核规则」按钮配置。\n要继续用默认规则审核吗？',
      '⚠️ 审核规则未配置',
      { confirmButtonText: '继续用默认', cancelButtonText: '去配置', type: 'warning' },
    ).catch(() => {
      showPromptDialog.value = true
      throw new Error('cancel')
    })
  }
  await aiReview(scope)
}
const hasSelection = computed(() => (crudRef.value?.crud?.selections?.value?.length ?? 0) > 0)
const selectionCount = computed(() => crudRef.value?.crud?.selections?.value?.length ?? 0)
const getSelectedIds = () => crudRef.value?.crud?.selections?.value?.map((r: any) => r.id) || []

// ---- 统计 ----

const statsData = ref<any>(null)

async function loadStats() {
  try {
    statsData.value = await get('/admin/keyword/stats')
  } catch { /* 统计加载失败静默 */ }
}

function filterByStage(stage: string) {
  crudRef.value?.crud?.setQuery?.({ stage })
  crudRef.value?.crud?.getList?.()
}

onMounted(() => { loadStats() })

// ---- 表格配置 ----

const columns: CrudColumn[] = [
  { field: 'id', label: 'ID', width: 60 },
  { field: 'keyword', label: '关键词', minWidth: 180 },
  { field: 'slug', label: '网址标识', minWidth: 120 },
  { field: 'source', label: '来源', width: 80, type: 'tag',
    tagMap: {
      manual: { label: '手动', type: 'info' },
      google: { label: 'Google', type: 'primary' },
      yandex: { label: 'Yandex', type: 'warning' },
      ddg:    { label: 'DDG', type: 'success' },
      baidu:  { label: '百度', type: 'primary' },
      sogou:  { label: '搜狗', type: 'warning' },
    } },
  { field: 'seed_keyword', label: '种子词', width: 140 },
  { field: 'stage', label: '阶段', width: 85, align: 'center', type: 'status',
    statusMap: {
      candidate: { label: '候选', type: 'warning' },
      approved:  { label: '已上线', type: 'success' },
      archived:  { label: '已忽略', type: 'info' },
    } },
  { field: 'expanded_as_seed_at', label: '已扩展', width: 75, align: 'center',
    formatter: (row: any) => row.expanded_as_seed_at ? '是' : '否' },
  { field: 'article_count', label: '文章', width: 60 },
  { field: 'color', label: '颜色', width: 60 },
]

const searchFields: SearchField[] = [
  { field: 'keyword', label: '搜索', type: 'input', placeholder: '关键词 / slug / 种子词' },
  { field: 'stage', label: '阶段', type: 'select', options: [
    { label: '候选', value: 'candidate' }, { label: '已上线', value: 'approved' },
    { label: '已忽略', value: 'archived' },
  ] },
  { field: 'source_code', label: '来源', type: 'select', options: [
    { label: '手动', value: 'manual' }, { label: 'Google', value: 'google' },
    { label: 'DuckDuckGo', value: 'ddg' }, { label: 'Yandex', value: 'yandex' },
    { label: '百度', value: 'baidu' }, { label: '搜狗', value: 'sogou' },
  ] },
]

const formFields: FormField[] = [
  { field: 'keyword', label: '关键词', rules: [{ required: true, message: '请输入' }] },
  { field: 'slug', label: '网址标识', placeholder: '留空自动生成' },
  { field: 'color', label: '颜色', type: 'color' },
  { field: 'description', label: '描述', type: 'textarea' },
  { field: 'sort', label: '排序', type: 'number', default: 0 },
  { field: 'stage', label: '阶段', type: 'select', default: 'candidate', options: [
    { label: '候选', value: 'candidate' }, { label: '上线', value: 'approved' },
    { label: '忽略', value: 'archived' },
  ] },
]

// ---- 批量操作 ----

async function bulkApprove() {
  const ids = getSelectedIds()
  await confirmDialog(
    `确认上线 ${ids.length} 个标签？上线后会生成 slug 并作为前台着陆页展示。`,
    '批量上线', { type: 'success' }
  )
  const res = await post('/admin/keyword/bulk-approve', { ids })
  ElMessage.success(`已上线 ${res?.approved ?? 0} 个标签`)
  refresh()
}

async function bulkReject() {
  const ids = getSelectedIds()
  await confirmDialog(
    `确认忽略 ${ids.length} 个标签？忽略后不会在前台展示，也不再参与轮询采集。`,
    '批量忽略', { type: 'warning' }
  )
  const res = await post('/admin/keyword/bulk-reject', { ids })
  ElMessage.success(`已忽略 ${res?.rejected ?? 0} 个`)
  refresh()
}

async function bulkOffline() {
  const ids = getSelectedIds()
  await confirmDialog(
    `确认下线 ${ids.length} 个标签？下线后将回到「待审核」状态，前台不再展示。`,
    '批量下线', { type: 'error' }
  )
  const res = await post('/admin/keyword/bulk-stage', { ids, stage: 'candidate' })
  ElMessage.success(`已下线 ${res?.updated ?? 0} 个`)
  refresh()
}

function refresh() {
  crudRef.value?.crud?.getList()
  loadStats()
}

// ---- 轮询采集 (SSE) ----

const polling = ref(false)

async function pollHarvest() {
  // 直接开跑，不 confirm（用户点按钮就是意图明确）
  harvestMode.value = 'poll'
  polling.value = true
  harvestLog.value = []
  harvestDone.value = false
  harvestTotal.value = 0
  showHarvest.value = true
  try {
    // max_total=5000 + batch_size=20：持续滚动到池空或 5000 上限
    await runHarvestSSE('/api/admin/keyword/poll-harvest-stream', { batch_size: 20, max_total: 5000 })
  } finally {
    polling.value = false
    refresh()
  }
}

function openManualHarvest() {
  harvestMode.value = 'manual'
  harvestSource.value = 'manual'
  harvestLog.value = []
  harvestDone.value = false
  harvestTotal.value = 0
  showHarvest.value = true
}

// ---- 手动采集 (SSE) ----

const showHarvest = ref(false)
const harvestMode = ref<'manual' | 'poll'>('manual')
const harvestSource = ref<'manual' | 'ai'>('manual')
const harvesting = ref(false)
const harvestForm = ref({ seedsText: '', engines: ['google', 'duckduckgo'] as string[] })
const seedCount = computed(() =>
  harvestForm.value.seedsText.split('\n').map(s => s.trim()).filter(Boolean).length
)

const harvestLog = ref<any[]>([])
const harvestDone = ref(false)
const harvestTotal = ref(0)
const logBody = ref<HTMLElement>()

async function doHarvest() {
  const seeds = harvestForm.value.seedsText.split('\n').map(s => s.trim()).filter(Boolean)
  if (!seeds.length) return ElMessage.warning('请输入种子关键词')
  if (seeds.length > 50) return ElMessage.warning('单次最多 50 个种子词')
  if (!harvestForm.value.engines.length) return ElMessage.warning('请选择至少一个搜索引擎')

  harvesting.value = true
  harvestLog.value = []
  harvestDone.value = false
  harvestTotal.value = 0

  try {
    await runHarvestSSE('/api/admin/keyword/harvest-stream', {
      seeds, engines: harvestForm.value.engines,
    })
  } finally {
    harvesting.value = false
    refresh()
  }
}

// ---- 采集 SSE 统一入口（复用手动/轮询） ----

async function runHarvestSSE(url: string, body: any) {
  const { run } = useSSE({
    url,
    onEvent: (evt) => {
      harvestLog.value.push(evt)
      if (evt.total !== undefined) harvestTotal.value = evt.total
      if (evt.type === 'done') {
        harvestDone.value = true
        ElMessage.success(`采集完成：${evt.imported} 个新标签入库`)
      }
      // 自动滚到底部
      nextTick(() => { logBody.value?.scrollTo(0, logBody.value.scrollHeight) })
    },
  })
  await run(body)
}

// ---- AI 种子词 ----

const aiLoading = ref(false)

async function getAiSeeds() {
  aiLoading.value = true
  try {
    const res = await post('/admin/keyword/ai-seeds', { topic: '关键词种子(可自行修改)', count: 20 })
    const seeds = res?.seeds || []
    if (!seeds.length) {
      ElMessage.warning('AI 未返回结果，已填入默认种子词')
    }
    harvestForm.value.seedsText = seeds.join('\n')
    harvestMode.value = 'manual'
    harvestSource.value = 'ai'
    showHarvest.value = true
    ElMessage.success(`AI 推荐了 ${seeds.length} 个种子词，检查后点"开始采集"`)
  } catch {
    ElMessage.error('AI 请求失败')
  } finally {
    aiLoading.value = false
  }
}

// ---- AI 审核（SSE 流式，处理所有待审核）----

const aiReviewing = ref(false)
const showAiResult = ref(false)
const aiLog = ref<any[]>([])
const aiSummary = ref<any>(null)
const aiLogBody = ref<HTMLElement>()

const aiScope = ref<'pending' | 'online' | 'all'>('pending')
const SCOPE_DESC: Record<string, { label: string; tip: string }> = {
  pending: { label: '待审核', tip: '只审 status=0 的标签：AI 判定 approve→上线，reject→删除，uncertain→保留等人工。' },
  online:  { label: '已上线', tip: '重审 status=1 已上线：AI reject→删除，uncertain→降回待审核，approve 保持上线。' },
  all:     { label: '全部', tip: '遍历 pending + online 全部标签，按上面的规则处理。大体量时较慢。' },
}
const aiScopeLabel = computed(() => SCOPE_DESC[aiScope.value].label)

async function aiReview(scope: 'pending' | 'online' | 'all') {
  const desc = SCOPE_DESC[scope]
  await confirmDialog(desc.tip, `AI 审核 — ${desc.label}`, { type: 'warning' })
  aiScope.value = scope
  aiReviewing.value = true
  showAiResult.value = true
  aiLog.value = []
  aiSummary.value = null

  const { run } = useSSE({
    url: '/api/admin/keyword/ai-review-stream',
    onEvent: (evt) => {
      aiLog.value.push(evt)
      if (evt.type === 'done') {
        aiSummary.value = evt
        const demoted = evt.demoted_total ? `，降回待审 ${evt.demoted_total}` : ''
        ElMessage.success(`审核完成：上线 ${evt.approved_total}，删除 ${evt.rejected_total}${demoted}，保留 ${evt.uncertain_total}`)
      }
      nextTick(() => { aiLogBody.value?.scrollTo(0, aiLogBody.value.scrollHeight) })
    },
    onError: (msg) => ElMessage.error(msg),
  })
  try {
    await run({ scope })
  } finally {
    aiReviewing.value = false
    refresh()
  }
}
</script>

<style scoped>
/* 审核规则弹窗 */
.prompt-intro {
  padding: 12px 16px; margin-bottom: 16px;
  background: #eff6ff; border-left: 3px solid #3b82f6; border-radius: 4px;
  font-size: 13px; color: #1e40af; line-height: 1.7;
}
.ai-assist {
  padding: 16px; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px;
  margin-bottom: 8px;
}
.ai-assist-title {
  font-size: 13px; font-weight: 700; color: #0f172a; margin-bottom: 12px;
}
.prompt-hint-line { font-size: 12px; color: #64748b; margin-top: 4px; line-height: 1.6; }
.prompt-hint-line code { background: #f1f5f9; padding: 1px 6px; border-radius: 3px; font-size: 11px; }

.stat-cards {
  display: flex;
  gap: 12px;
  margin-bottom: 16px;
}
.stat-card {
  flex: 1;
  padding: 16px;
  border-radius: 8px;
  text-align: center;
  cursor: pointer;
  transition: transform 0.15s;
  border: 1px solid var(--el-border-color-lighter);
  background: var(--el-bg-color);
}
.stat-card:hover { transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,0.08); }
.stat-num { font-size: 28px; font-weight: 700; line-height: 1.2; }
.stat-label { font-size: 13px; color: var(--el-text-color-secondary); margin-top: 4px; }
.stat-card.pending .stat-num { color: var(--el-color-warning); }
.stat-card.online .stat-num { color: var(--el-color-success); }
.stat-card.ignored .stat-num { color: var(--el-color-info); }
.stat-card.harvest .stat-num { color: var(--el-color-danger); }
.stat-card.total .stat-num { color: var(--el-color-primary); }
.form-hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 4px;
}
.harvest-tip {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 12px 16px;
  margin-bottom: 16px;
  border-radius: 6px;
  background: var(--el-fill-color-light);
  color: var(--el-text-color-regular);
  font-size: 13px;
  line-height: 1.6;
}
.tip-icon { flex-shrink: 0; font-size: 15px; }
.harvest-log {
  margin-top: 16px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  overflow: hidden;
}
.log-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  background: var(--el-fill-color-light);
  font-size: 13px;
  font-weight: 600;
}
.log-body {
  max-height: 240px;
  overflow-y: auto;
  padding: 8px 12px;
  font-size: 12px;
  line-height: 1.8;
  font-family: 'SF Mono', Monaco, Menlo, monospace;
}
.log-line.level { color: var(--el-color-primary); font-weight: 600; }
.log-line.seed { color: var(--el-text-color-secondary); }
.log-line.found { color: var(--el-color-success-dark-2); }
.log-line.done { color: var(--el-color-primary); font-weight: 600; margin-top: 4px; }
.kw-list {
  display: inline;
  margin-left: 6px;
  color: var(--el-text-color-regular);
  font-size: 11px;
}
.ai-summary {
  display: flex;
  gap: 20px;
  margin-bottom: 12px;
  font-size: 15px;
  font-weight: 600;
}
.ai-summary .approve { color: var(--el-color-success); }
.ai-summary .reject { color: var(--el-color-danger); }
.ai-summary .uncertain { color: var(--el-color-warning); }
.ai-review-log {
  max-height: 400px;
  overflow-y: auto;
  padding: 8px 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  font-size: 13px;
  line-height: 1.6;
}
.review-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin: 2px 8px 2px 0;
}
.kw-text { font-size: 12px; }
</style>
