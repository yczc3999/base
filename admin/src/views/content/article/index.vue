<template>
  <div>
    <!-- 采集统计 -->
    <div v-if="stats" class="stat-cards">
      <StatCard
        :icon="Sparkles" :value="stats.unprocessed" label="待润色" accent="var(--warning)" :count="true"
        @click="filterBy({source: 1, ai_processed: false})"
      />
      <StatCard
        :icon="CheckCheck" :value="stats.ai_done" label="已润色" accent="var(--success)" :count="true"
        @click="filterBy({source: 1, ai_processed: true})"
      />
      <StatCard
        :icon="Send" :value="stats.published" label="已发布" accent="var(--primary)" :count="true"
        @click="filterBy({status: 1})"
      />
      <StatCard
        :icon="Database" :value="stats.collected" label="采集总量" accent="var(--info)" :count="true"
      />
    </div>

    <CrudTable
ref="crudRef" api="admin/article" perms="admin:article"
      :columns="columns" :search-fields="searchFields"
      :has-create="false" :has-edit="false" :has-delete="false">

      <template #toolbar>
        <el-button type="primary" :icon="Plus" @click="openEditor()">新增</el-button>
        <el-button :icon="Download" @click="showCollect = true">采集文章</el-button>
        <el-button type="primary" :icon="MagicStick" @click="showTagGen = true">按标签生成</el-button>
        <el-button
type="warning" :icon="MagicStick" :disabled="!hasSelection"
          @click="aiRewrite">AI 润色 ({{ selectionCount }})</el-button>
      </template>

      <template #actions="{ row }">
        <el-button link type="primary" @click="openEditor(row)">编辑</el-button>
        <el-tag v-if="row.source === 1 && !row.ai_processed" type="warning" size="small">未润色</el-tag>
      </template>
    </CrudTable>

    <!-- 文章编辑器组件（编辑 + AI 助手）-->
    <ArticleEditor
      :visible="editorVisible"
      :data="editorData"
      :tags="tagOptions"
      :tags-loading="tagsLoading"
      @close="editorVisible = false"
      @saved="refresh"
      @load-tags="loadTags"
    />

    <!-- 采集弹窗 -->
    <el-dialog v-model="showCollect" title="采集文章" width="700px" :close-on-click-modal="false">
      <el-form v-if="!collecting" label-width="80px">
        <el-form-item label="采集方式">
          <el-radio-group v-model="collectForm.mode">
            <el-radio label="keyword">按关键词</el-radio>
            <el-radio label="url">指定 URL</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="collectForm.mode === 'keyword'" label="关键词">
          <el-input v-model="collectForm.keyword" placeholder="如：海外看B站教程" />
        </el-form-item>
        <el-form-item v-else label="URL">
          <el-input v-model="collectForm.url" placeholder="https://..." />
        </el-form-item>
        <el-form-item v-if="collectForm.mode === 'keyword'" label="数量">
          <el-input-number v-model="collectForm.count" :min="1" :max="10" />
          <span style="margin-left:8px;font-size:12px;color:var(--el-text-color-secondary)">逐页分析，只采正式文章</span>
        </el-form-item>
      </el-form>

      <div v-if="collectLog.length" class="sse-log">
        <div class="log-header">
          <span>采集进度</span>
          <el-tag :type="collectDone ? 'success' : 'warning'" size="small">
            {{ collectDone ? `完成 ${collectSaved} 篇` : '进行中' }}
          </el-tag>
        </div>
        <div ref="logBodyRef" class="log-body">
          <div v-for="(evt, i) in collectLog" :key="i" class="log-line" :class="evt.type">
            <template v-if="evt.type === 'search'"> 搜索 "{{ evt.keyword }}"</template>
            <template v-else-if="evt.type === 'search_done'"> 找到 {{ evt.found }} 个结果</template>
            <template v-else-if="evt.type === 'analyzing'">分析 #{{ evt.index }}：{{ evt.url }}</template>
            <template v-else-if="evt.type === 'extracted'"> <strong>{{ evt.title }}</strong>（{{ evt.word_count }}字）</template>
            <template v-else-if="evt.type === 'saved'"> 入库：{{ evt.title }}</template>
            <template v-else-if="evt.type === 'skip'"> 跳过：{{ evt.reason }}</template>
            <template v-else-if="evt.type === 'error'"> {{ evt.msg }}</template>
            <template v-else-if="evt.type === 'done'"> 完成：{{ evt.saved }}/{{ evt.total }} 篇</template>
          </div>
        </div>
      </div>

      <template #footer>
        <el-button :disabled="collecting" @click="showCollect = false">关闭</el-button>
        <el-button v-if="!collectDone" type="primary" :loading="collecting" @click="doCollect">
          {{ collecting ? '采集中...' : '开始采集' }}
        </el-button>
        <el-button v-else type="primary" @click="showCollect = false; refresh()">完成</el-button>
      </template>
    </el-dialog>

    <!-- AI 润色进度 -->
    <el-dialog v-model="showRewrite" title="AI 润色" width="650px" :close-on-click-modal="false" :close-on-press-escape="false">
      <div v-if="rewriting" style="margin-bottom:10px;padding:8px 12px;background:var(--el-color-warning-light-9);border-radius:4px;font-size:13px;color:var(--el-color-warning-dark-2)">
         润色进行中，每篇文章需要 30-60 秒，请勿关闭此窗口。
      </div>
      <div class="sse-log">
        <div class="log-header">
          <span>润色进度</span>
          <el-tag :type="rewriteDone ? 'success' : 'warning'" size="small">
            {{ rewriteDone ? '完成' : '处理中' }}
          </el-tag>
        </div>
        <div ref="rewriteLogRef" class="log-body">
          <div v-for="(evt, i) in rewriteLog" :key="i" class="log-line" :class="evt.type">
            <template v-if="evt.type === 'rewriting'"> 开始处理：{{ evt.title }}</template>
            <template v-else-if="evt.type === 'step'">
              <span class="step-indicator"> {{ evt.step }}</span>
            </template>
            <template v-else-if="evt.type === 'done_one'"> <strong>{{ evt.new_title }}</strong>（{{ evt.word_count }}字）</template>
            <template v-else-if="evt.type === 'error'">
              <span style="color:var(--el-color-danger)"> 失败：{{ evt.msg }}</span>
            </template>
            <template v-else-if="evt.type === 'done'"> 完成 {{ evt.processed }}/{{ evt.total }} 篇</template>
          </div>
        </div>
      </div>
      <template #footer>
        <el-button :disabled="rewriting" @click="showRewrite = false; refresh()">关闭</el-button>
      </template>
    </el-dialog>

    <!-- 按标签生成文章 -->
    <el-dialog v-model="showTagGen" title="按标签生成文章" width="700px" :close-on-click-modal="false">
      <div v-if="!tagGenRunning" style="margin-bottom:12px;color:var(--el-text-color-secondary);font-size:13px">
        选择已上线的标签，AI 为每个标签生成一篇文章（草稿）。
      </div>
      <div v-if="!tagGenRunning">
        <el-select
v-model="tagGenIds" multiple filterable placeholder="选择标签（可多选）" style="width:100%"
          :loading="tagsLoading" @focus="loadTags">
          <el-option v-for="t in tagOptions" :key="t.id" :label="t.name" :value="t.id" />
        </el-select>
      </div>
      <div v-if="tagGenLog.length" class="sse-log" style="margin-top:12px">
        <div class="log-header">
          <span>生成进度</span>
          <el-tag :type="tagGenDone ? 'success' : 'warning'" size="small">
            {{ tagGenDone ? '完成' : '生成中' }}
          </el-tag>
        </div>
        <div class="log-body">
          <div v-for="(evt, i) in tagGenLog" :key="i" class="log-line" :class="evt.type">
            <template v-if="evt.type === 'start'">准备生成 {{ evt.total }} 篇</template>
            <template v-else-if="evt.type === 'generating'"> [{{ evt.index }}/{{ evt.total }}] {{ evt.tag }}</template>
            <template v-else-if="evt.type === 'created'"> {{ evt.title }}</template>
            <template v-else-if="evt.type === 'error'"> {{ evt.tag }}：{{ evt.msg }}</template>
            <template v-else-if="evt.type === 'done'"> 完成 {{ evt.created }}/{{ evt.total }} 篇</template>
          </div>
        </div>
      </div>
      <template #footer>
        <el-button :disabled="tagGenRunning" @click="showTagGen = false; refresh()">关闭</el-button>
        <el-button
v-if="!tagGenDone" type="primary" :loading="tagGenRunning"
          :disabled="!tagGenIds.length || tagGenRunning" @click="doTagGen">
          生成 {{ tagGenIds.length }} 篇文章
        </el-button>
      </template>
    </el-dialog>

  </div>
</template>

<script setup lang="ts">
defineOptions({ name: 'ContentArticle' })
import { ref, computed, onMounted, defineAsyncComponent } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index'
import { confirmDialog } from '@/utils/confirm'
import { Download, MagicStick, Plus } from '@element-plus/icons-vue'
import { Sparkles, CheckCheck, Send, Database } from 'lucide-vue-next'
import StatCard from '@/components/StatCard/index.vue'
import CrudTable from '@/components/CrudTable/index.vue'
import type { CrudColumn, SearchField } from '@/components/CrudTable/types'
import { get } from '@/api/request'
import { useSSE } from '@/hooks/useSSE'

const ArticleEditor = defineAsyncComponent(() => import('@/components/ArticleEditor/index.vue'))

const crudRef = ref()
const hasSelection = computed(() => (crudRef.value?.crud?.selections?.value?.length ?? 0) > 0)
const selectionCount = computed(() => crudRef.value?.crud?.selections?.value?.length ?? 0)

const stats = ref<any>(null)
async function loadStats() { try { stats.value = await get('/admin/article/collect-stats') } catch { /* 统计加载失败静默 */ } }
onMounted(loadStats)

function filterBy(q: Record<string, any>) { crudRef.value?.crud?.setQuery?.(q); crudRef.value?.crud?.getList?.() }
function refresh() { crudRef.value?.crud?.getList(); loadStats() }

const columns: CrudColumn[] = [
  { field: 'id', label: 'ID', width: 60, sortable: 'custom' },
  { field: 'title', label: '标题', minWidth: 250 },
  { field: 'source', label: '来源', width: 70, type: 'tag',
    tagMap: { 0: { label: '手动', type: 'info' }, 1: { label: '采集', type: 'warning' } } },
  { field: 'ai_processed', label: 'AI', width: 55, align: 'center', type: 'status',
    statusMap: { true: { label: '✓', type: 'success' }, false: { label: '—', type: 'info' } } },
  { field: 'status', label: '状态', width: 75, align: 'center', type: 'status',
    statusMap: { 0: { label: '草稿', type: 'info' }, 1: { label: '发布', type: 'success' } } },
  { field: 'view_count', label: '浏览', width: 60 },
  { field: 'published_at', label: '发布时间', width: 160, type: 'time', sortable: 'custom' },
  { field: 'created_at', label: '创建', width: 160, type: 'time', sortable: 'custom' },
]

const searchFields: SearchField[] = [
  { field: 'keyword', label: '搜索', type: 'input', placeholder: '标题' },
  { field: 'status', label: '状态', type: 'select', options: [
    { label: '草稿', value: 0 }, { label: '已发布', value: 1 },
  ] },
  { field: 'source', label: '来源', type: 'select', options: [
    { label: '手动', value: 0 }, { label: '采集', value: 1 },
  ] },
]

// ---- 编辑器 ----

const editorVisible = ref(false)
const editorData = ref<any>(null)

async function openEditor(row?: any) {
  if (row?.id) {
    try { editorData.value = await get('/admin/article/getDetail', { id: row.id }) }
    catch { editorData.value = { ...row } }
  } else {
    editorData.value = null
  }
  editorVisible.value = true
}

// ---- 按标签生成 ----

const showTagGen = ref(false)
const tagGenIds = ref<number[]>([])
const tagGenRunning = ref(false)
const tagGenDone = ref(false)
const tagGenLog = ref<any[]>([])
const tagOptions = ref<any[]>([])
const tagsLoading = ref(false)

async function loadTags() {
  if (tagOptions.value.length) return
  tagsLoading.value = true
  try {
    const res = await get('/admin/keyword/getList', { pageSize: 500, filters: JSON.stringify({ stage: 'approved' }) })
    tagOptions.value = res?.list || []
  } finally { tagsLoading.value = false }
}

async function doTagGen() {
  if (!tagGenIds.value.length) return
  tagGenRunning.value = true
  tagGenDone.value = false
  tagGenLog.value = []
  const { run } = useSSE({
    url: '/api/admin/article/gen-from-tags-stream',
    onEvent: (evt) => { tagGenLog.value.push(evt) },
    onDone: () => {
      tagGenDone.value = true
      const d = tagGenLog.value.find((e: any) => e.type === 'done')
      ElMessage.success(`生成完成：${d?.created ?? 0}/${d?.total ?? 0} 篇`)
    },
  })
  await run({ tag_ids: tagGenIds.value })
  tagGenRunning.value = false
}

// ---- 采集 ----

const showCollect = ref(false)
const collecting = ref(false)
const collectDone = ref(false)
const collectSaved = ref(0)
const collectLog = ref<any[]>([])
const collectForm = ref({ mode: 'keyword', keyword: '', url: '', count: 5 })

async function doCollect() {
  const f = collectForm.value
  if (f.mode === 'keyword' && !f.keyword) return ElMessage.warning('请输入关键词')
  if (f.mode === 'url' && !f.url) return ElMessage.warning('请输入 URL')
  collecting.value = true; collectDone.value = false; collectLog.value = []; collectSaved.value = 0
  const { run } = useSSE({
    url: '/api/admin/article/collect-stream',
    onEvent: (evt) => { collectLog.value.push(evt) },
    onDone: () => {
      collectDone.value = true
      collectSaved.value = collectLog.value.find(e => e.type === 'done')?.saved || 0
    },
  })
  await run(f)
  collecting.value = false
}

// ---- AI 润色 ----

const showRewrite = ref(false)
const rewriting = ref(false)
const rewriteDone = ref(false)
const rewriteLog = ref<any[]>([])

async function aiRewrite() {
  const rows = crudRef.value?.crud?.selections?.value || []
  if (!rows.length) return
  await confirmDialog(
    `对 ${rows.length} 篇文章 AI 深度润色？每篇独立请求，过程中请勿关闭。`, 'AI 批量润色', { type: 'warning' })

  showRewrite.value = true
  rewriting.value = true
  rewriteDone.value = false
  rewriteLog.value = []
  const { run } = useSSE({
    url: '/api/admin/article/ai-rewrite-stream',
    onEvent: (evt) => { rewriteLog.value.push(evt) },
    onDone: () => {
      rewriteDone.value = true
      const d = rewriteLog.value.find((e: any) => e.type === 'done')
      ElMessage.success(`润色完成：${d?.processed ?? 0}/${d?.total ?? 0} 篇`)
    },
  })
  await run({ ids: rows.map((r: any) => r.id) })
  rewriting.value = false
}
</script>

<style scoped>
.stat-cards { display: grid; grid-template-columns: repeat(4, 1fr); gap: var(--space-base); margin-bottom: var(--space-base); }
.sse-log { margin-top: var(--space-md); border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden; }
.log-header { display: flex; align-items: center; justify-content: space-between; padding: var(--space-sm) var(--space-md); background: var(--bg-subtle); font-size: var(--text-sm); font-weight: 600; }
.log-body { max-height: 280px; overflow-y: auto; padding: var(--space-sm) var(--space-md); font-size: var(--text-xs); line-height: 1.8; }
.log-line.saved, .log-line.done_one { color: var(--el-color-success-dark-2); }
.log-line.step { color: var(--el-color-primary); }
.step-indicator { animation: pulse 1.5s infinite; }
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.5; } }
.log-line.skip { color: var(--el-text-color-secondary); }
.log-line.error { color: var(--el-color-danger); }
.log-line.done { color: var(--el-color-primary); font-weight: 600; }

.ai-result-md :deep(strong) { font-weight: 600; }
</style>

<style>
/* ---- 全屏编辑层 ---- */
.editor-layer {
  position: fixed; inset: 0; z-index: 2000;
  background: var(--overlay-strong);
  display: flex; padding: var(--space-base); gap: var(--space-md);
  justify-content: center;
}
.editor-main {
  flex: 1; max-width: 820px; background: var(--bg-card); border-radius: var(--radius);
  display: flex; flex-direction: column; overflow: hidden;
}
.editor-top {
  display: flex; justify-content: space-between; align-items: center;
  padding: var(--space-md) var(--space-lg); border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
.editor-top h3 { margin: 0; font-size: var(--text-lg); }
.editor-form { padding: var(--space-base) var(--space-lg); overflow-y: auto; flex: 1; }
.editor-ai {
  width: 380px; flex-shrink: 0; background: var(--bg-card); border-radius: var(--radius);
  display: flex; flex-direction: column; overflow: hidden;
}
.ai-panel-header {
  padding: var(--space-md) var(--space-base); background: var(--primary); color: white;
  font-size: var(--text-base); font-weight: 600; flex-shrink: 0;
}
.ai-panel-body { padding: var(--space-md); overflow-y: auto; flex: 1; }
</style>
