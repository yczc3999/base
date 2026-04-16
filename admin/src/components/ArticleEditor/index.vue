<template>
  <div v-if="visible" class="editor-backdrop">
    <div class="editor-layout">
      <!-- ===== 左面板：文章编辑 ===== -->
      <main class="editor-main">
        <!-- 标题区 -->
        <div class="title-area">
          <input v-model="form.title" class="title-input" placeholder="输入文章标题..." />
          <button class="ai-pill" :class="{ working: aiLoading.title }" @click="aiGenTitle">
            <span v-if="aiLoading.title" class="spin-icon">⟳</span>
            <span v-else>AI ✨</span>
          </button>
        </div>

        <!-- 元信息折叠 -->
        <div class="meta-section">
          <details class="meta-details">
            <summary class="meta-summary">
              <span>📋 元信息 (slug / 摘要 / 封面)</span>
              <span class="chevron">▾</span>
            </summary>
            <div class="meta-body">
              <div class="meta-field">
                <label>URL SLUG</label>
                <div class="field-with-ai">
                  <input v-model="form.slug" class="mono-input" placeholder="english-url-slug" />
                  <button class="ai-mini" :class="{ working: aiLoading.slug }" @click="aiGenSlug">
                    <span v-if="aiLoading.slug" class="spin-icon">⟳</span><span v-else>✨</span>
                  </button>
                </div>
              </div>
              <div class="meta-field">
                <label>摘要</label>
                <div class="field-with-ai">
                  <textarea v-model="form.summary" rows="2" placeholder="文章简介..."></textarea>
                  <button class="ai-mini" :class="{ working: aiLoading.summary }" @click="aiGenSummary">
                    <span v-if="aiLoading.summary" class="spin-icon">⟳</span><span v-else>✨</span>
                  </button>
                </div>
              </div>
              <div class="meta-field">
                <label>封面图</label>
                <ImageUpload v-model="form.cover_image" :limit="1" category="article" hint="建议 16:9" />
              </div>
            </div>
          </details>
        </div>

        <!-- 富文本编辑器 -->
        <div class="editor-area">
          <RichEditor v-model="form.content" placeholder="写点什么..." />
        </div>

        <!-- 底栏 -->
        <div class="bottom-bar">
          <div class="bar-left">
            <!-- 状态 indicator：草稿/定时(含时间,可点)/已发布 -->
            <div class="status-indicator" :class="[`s-${statusKind}`, statusKind === 'scheduled' ? 'clickable' : '']"
              @click="statusKind === 'scheduled' && openScheduleDialog()"
              :title="statusKind === 'scheduled' ? '点击修改定时时间' : ''">
              {{ statusLabel }}
            </div>
            <div class="sort-field">
              <span>排序</span>
              <input v-model.number="form.sort" type="number" min="0" />
            </div>
          </div>
          <div class="bar-right">
            <label class="pin-toggle">
              <span>置顶</span>
              <input type="checkbox" v-model="form.is_pinned" />
              <div class="toggle-track"><div class="toggle-thumb"></div></div>
            </label>
            <button class="btn-cancel" @click="$emit('close')">取消</button>
            <button class="btn-save btn-draft" :disabled="saving" @click="saveAs('draft')">存草稿</button>
            <button class="btn-save btn-schedule" :disabled="saving"
              @click="openScheduleDialog" title="定时发布：点击选择时间">
              定时发布
            </button>
            <button class="btn-save" :class="{ loading: saving }" :disabled="saving" @click="saveAs('publish')">
              {{ saving ? '保存中...' : '立即发布' }}
            </button>
          </div>
        </div>
      </main>

      <!-- ===== 定时发布 Dialog ===== -->
      <div v-if="scheduleDialogVisible" class="schedule-modal" @click.self="scheduleDialogVisible = false">
        <div class="schedule-card">
          <div class="schedule-title">⏰ 定时发布</div>
          <div class="schedule-hint">选择发布时间，到点系统会自动发布。此前保持草稿状态。</div>
          <input v-model="scheduleDraft" type="datetime-local" step="60" class="schedule-input" />
          <div v-if="scheduleDraftPreview" class="schedule-preview">
            将在 <strong>{{ scheduleDraftPreview }}</strong> 自动发布
          </div>
          <div class="schedule-footer">
            <button v-if="form.scheduled_at" class="btn-cancel btn-danger-text"
              :disabled="saving" @click="cancelSchedule">取消定时</button>
            <div class="schedule-footer-right">
              <button class="btn-cancel" @click="scheduleDialogVisible = false">关闭</button>
              <button class="btn-save" :disabled="saving || !isDraftFuture" @click="confirmSchedule">
                {{ saving && lastAction === 'schedule' ? '保存中...' : '确定' }}
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- ===== 右面板：AI 助手 ===== -->
      <aside class="ai-aside">
        <header class="ai-header">
          <span>✨ AI 写作助手</span>
        </header>

        <div class="ai-scroll">
          <!-- 关键词 -->
          <div class="ai-zone">
            <label class="zone-label"><i class="dot"></i>关键词</label>
            <div class="kw-row">
              <el-select v-model="topic.tagId" placeholder="搜索/选上线标签" size="small"
                filterable remote clearable
                :remote-method="searchTags" :loading="tagSearchLoading"
                popper-class="ai-kw-popper"
                @change="onTagSelect" @visible-change="onTagDropdownOpen"
                style="flex:0 0 40%">
                <el-option v-for="t in tagSearchResults" :key="t.id" :label="t.name" :value="t.id" />
                <template #empty>
                  <div class="tag-empty">{{ tagSearchLoading ? '搜索中...' : '未找到上线标签' }}</div>
                </template>
              </el-select>
              <input v-model="topic.keyword" placeholder="关键词（可手动编辑）" />
            </div>
          </div>

          <!-- 生成 -->
          <div class="ai-zone">
            <button class="gen-btn" :class="{ working: generating }" :disabled="!currentKeyword || generating" @click="generate">
              <template v-if="generating">
                <span class="btn-dots"><i></i><i></i><i></i></span>
                <span>{{ stage }}</span>
              </template>
              <template v-else>生成「{{ currentKeyword || '...' }}」的文章</template>
            </button>
          </div>

          <!-- 结果 -->
          <div class="ai-zone" v-if="result">
            <div class="result-card">
              <div class="result-chips">
                <button class="chip-primary" @click="applyResult('replace')">✓ 用这篇</button>
                <button class="chip-outline" @click="generate">↻ 重写</button>
                <button class="chip-outline" @click="applyResult('append')">+ 追加</button>
                <button class="chip-icon" @click="copy" title="复制">📋</button>
              </div>
              <div class="result-content" v-html="resultHtml"></div>
            </div>
          </div>
        </div>

        <!-- 聊天输入 -->
        <div class="chat-bar">
          <div class="chat-input-wrap">
            <input v-model="chatInput" placeholder="向 AI 提问或润色..."
              @keyup.enter="sendChat" />
            <button class="send-btn" :disabled="generating" @click="sendChat">
              <span>➤</span>
            </button>
          </div>
        </div>
      </aside>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, watch, defineAsyncComponent } from 'vue'
import { ElMessage } from 'element-plus'
import { post, get } from '@/api/request'
import ImageUpload from '@/components/ImageUpload/index.vue'
import MarkdownIt from 'markdown-it'

const RichEditor = defineAsyncComponent(() => import('@/components/RichEditor/index.vue'))
const md = new MarkdownIt({ html: true, breaks: true, linkify: true })

const props = defineProps<{
  visible: boolean
  data?: Record<string, any> | null
  tags: any[]
  tagsLoading: boolean
}>()

const emit = defineEmits<{ close: []; saved: []; 'load-tags': [] }>()

// ---- Form ----
const saving = ref(false)
const form = reactive({
  id: null as number | null,
  title: '', slug: '', summary: '', cover_image: '', content: '',
  status: 0, sort: 0, is_pinned: false,
  published_at: null as string | null,  // 实际发布时间（只读展示，后端 status=1 钩子自动填）
  scheduled_at: null as string | null,  // 计划发布时间（status=0 + 未来 = 定时）
})
const lastAction = ref<'draft' | 'publish' | 'schedule' | null>(null)

const _resetForm = (d: any) => {
  if (d) {
    Object.assign(form, d)
    form.status = Number(form.status) || 0
    form.sort = Number(form.sort) || 0
  } else {
    Object.assign(form, {
      id: null, title: '', slug: '', summary: '', cover_image: '', content: '',
      status: 0, sort: 0, is_pinned: false, published_at: null, scheduled_at: null,
    })
  }
}
watch(() => props.data, _resetForm, { immediate: true })
watch(() => props.visible, (v) => {
  if (v) { _resetForm(props.data); result.value = ''; resultHtml.value = '' }
})

// 状态识别（v2 语义：scheduled_at 是真相源）
function toTs(s: string | null): number {
  return s ? new Date(String(s).replace(' ', 'T')).getTime() : 0
}
function formatDate(s: string | null): string {
  return s ? String(s).replace('T', ' ').slice(0, 16) : ''
}

const statusKind = computed<'draft' | 'scheduled' | 'live'>(() => {
  if (+form.status === 1) return 'live'
  if (form.scheduled_at && toTs(form.scheduled_at) > Date.now()) return 'scheduled'
  return 'draft'
})
const statusLabel = computed(() => ({
  draft: '📝 草稿',
  scheduled: `⏰ 定时 ${formatDate(form.scheduled_at)}`,
  live: `✅ 已发布 ${formatDate(form.published_at)}`,
}[statusKind.value]))

// ---- 定时 Dialog ----
const scheduleDialogVisible = ref(false)
const scheduleDraft = ref<string>('')  // datetime-local 格式 YYYY-MM-DDTHH:mm

const isDraftFuture = computed(() => {
  if (!scheduleDraft.value) return false
  return toTs(scheduleDraft.value) > Date.now()
})
const scheduleDraftPreview = computed(() => {
  if (!isDraftFuture.value) return ''
  return formatDate(scheduleDraft.value)
})

function openScheduleDialog() {
  // 预填：若已有 scheduled_at 用它；否则默认明天 09:00
  if (form.scheduled_at) {
    scheduleDraft.value = String(form.scheduled_at).replace(' ', 'T').slice(0, 16)
  } else {
    const t = new Date()
    t.setDate(t.getDate() + 1); t.setHours(9, 0, 0, 0)
    scheduleDraft.value = t.toISOString().slice(0, 16)
  }
  scheduleDialogVisible.value = true
}

async function confirmSchedule() {
  if (!isDraftFuture.value) return ElMessage.warning('请选未来的时间')
  if (!form.title) return ElMessage.warning('请先输入标题')
  if (!form.content) return ElMessage.warning('请先输入正文')
  form.status = 0
  // datetime-local 'YYYY-MM-DDTHH:mm' → 后端 'YYYY-MM-DD HH:mm:ss'
  form.scheduled_at = scheduleDraft.value.replace('T', ' ') + ':00'
  lastAction.value = 'schedule'
  await _doSave('schedule')
  scheduleDialogVisible.value = false
}

async function cancelSchedule() {
  form.scheduled_at = null
  form.status = 0
  lastAction.value = 'draft'
  await _doSave('draft')
  scheduleDialogVisible.value = false
}

// ---- 保存 ----
async function saveAs(action: 'draft' | 'publish') {
  if (!form.title) return ElMessage.warning('请输入标题')
  if (action === 'draft') {
    form.status = 0
    // 存草稿保留 scheduled_at（不偷偷取消定时）
  } else if (action === 'publish') {
    form.status = 1
    form.published_at = null   // 让后端钩子填 NOW
    form.scheduled_at = null   // 立即发布清计划时间
  }
  lastAction.value = action
  await _doSave(action)
}

async function _doSave(action: 'draft' | 'publish' | 'schedule') {
  saving.value = true
  try {
    await post('/admin/article/doEdit', form)
    const msg = action === 'draft' ? '已存为草稿'
      : action === 'publish' ? '已发布'
      : `已定时：${formatDate(form.scheduled_at)} 自动发布`
    ElMessage.success(msg)
    emit('saved')
    emit('close')
  } catch (e: any) { ElMessage.error(e?.message || '保存失败') }
  finally { saving.value = false }
}

// ---- AI quick buttons ----
const aiLoading = reactive({ title: false, slug: false, summary: false })

function typewriterField(target: { value: string } | any, key: string, text: string): Promise<void> {
  return new Promise((resolve) => {
    if (key === '_direct') { target.value = ''; }
    else { target[key] = '' }
    let i = 0
    const step = Math.max(1, Math.floor(text.length / 60))
    const timer = setInterval(() => {
      i = Math.min(i + step, text.length)
      if (key === '_direct') target.value = text.slice(0, i)
      else target[key] = text.slice(0, i)
      if (i >= text.length) { clearInterval(timer); resolve() }
    }, 25)
  })
}

async function aiGenTitle() {
  if (!form.content && !form.summary) return ElMessage.warning('先写点正文')
  aiLoading.title = true
  try {
    const r = await post('/admin/article/ai-generate', { type: 'title', content: (form.content || form.summary).slice(0, 2000) })
    if (r?.result) await typewriterField(form, 'title', r.result)
  } finally { aiLoading.title = false }
}
async function aiGenSlug() {
  if (!form.title) return ElMessage.warning('先填标题')
  aiLoading.slug = true
  try {
    const r = await post('/admin/article/ai-generate', { type: 'slug', title: form.title })
    if (r?.result) await typewriterField(form, 'slug', r.result)
  } finally { aiLoading.slug = false }
}
async function aiGenSummary() {
  if (!form.content) return ElMessage.warning('先写正文')
  aiLoading.summary = true
  try {
    const r = await post('/admin/article/ai-generate', { type: 'summary', content: form.content.slice(0, 3000) })
    if (r?.result) await typewriterField(form, 'summary', r.result)
  } finally { aiLoading.summary = false }
}

// ---- AI Assistant ----
const topic = reactive({ tagId: null as number | null, keyword: '' })
const generating = ref(false)
const stage = ref('')
const result = ref('')
const resultHtml = ref('')
const chatInput = ref('')

// 生成永远以 keyword 输入框为准（允许手动编辑覆盖标签选择）
const currentKeyword = computed(() => topic.keyword.trim())

// ---- 标签远程搜索（只搜 status=1 上线标签，支持任意规模）----
const tagSearchResults = ref<any[]>([])
const tagSearchLoading = ref(false)
let _tagSearchTimer: any = null
let _tagSearchSeq = 0

async function _fetchTags(keyword: string) {
  const seq = ++_tagSearchSeq
  tagSearchLoading.value = true
  try {
    const params: any = {
      pageSize: 30,
      filters: JSON.stringify({ status: 1 }),
    }
    if (keyword) params.keyword = keyword
    const res = await get('/admin/tag/getList', params)
    // 防竞态：只采用最新请求的结果
    if (seq !== _tagSearchSeq) return
    tagSearchResults.value = res?.list || []
  } finally {
    if (seq === _tagSearchSeq) tagSearchLoading.value = false
  }
}

function searchTags(keyword: string) {
  clearTimeout(_tagSearchTimer)
  _tagSearchTimer = setTimeout(() => _fetchTags(keyword.trim()), 250)
}

function onTagDropdownOpen(visible: boolean) {
  // 首次展开且结果为空时加载 Top-30 上线标签（按默认排序）
  if (visible && tagSearchResults.value.length === 0) _fetchTags('')
}

function onTagSelect() {
  const t = tagSearchResults.value.find((x: any) => x.id === topic.tagId)
  if (t) topic.keyword = t.name
}

async function generate() {
  if (!currentKeyword.value) return
  generating.value = true
  result.value = ''
  resultHtml.value = ''
  const stages = ['分析关键词...', '规划结构...', '撰写正文...', '润色中...']
  let idx = 0
  stage.value = stages[0]
  const timer = setInterval(() => { idx = Math.min(idx + 1, stages.length - 1); stage.value = stages[idx] }, 5000)
  try {
    const r = await post('/admin/article/ai-generate', { type: 'gen_article', keyword: currentKeyword.value })
    await typewriter(r?.result || '')
  } catch (e: any) { ElMessage.error(e?.message || '生成失败') }
  finally { clearInterval(timer); generating.value = false }
}

async function sendChat() {
  if (!chatInput.value.trim()) return
  generating.value = true
  try {
    const r = await post('/admin/article/ai-generate', {
      type: 'chat',
      message: chatInput.value,
      prior_content: result.value || '',
    })
    chatInput.value = ''
    await typewriter(r?.result || '')
  } catch (e: any) { ElMessage.error(e?.message || '失败') }
  finally { generating.value = false }
}

function typewriter(text: string): Promise<void> {
  return new Promise((resolve) => {
    result.value = ''
    resultHtml.value = ''
    let i = 0
    const step = Math.max(3, Math.floor(text.length / 200))
    const timer = setInterval(() => {
      i = Math.min(i + step, text.length)
      result.value = text.slice(0, i)
      resultHtml.value = md.render(result.value)
      if (i >= text.length) { clearInterval(timer); resolve() }
    }, 20)
  })
}

function applyResult(mode: 'replace' | 'append') {
  if (!result.value) return
  const html = md.render(result.value)
  form.content = mode === 'replace' ? html : (form.content || '') + html
  ElMessage.success(mode === 'replace' ? '已填入正文' : '已追加')
}

function copy() {
  navigator.clipboard.writeText(result.value)
  ElMessage.success('已复制')
}
</script>

<style scoped>
/* ===== 全局 ===== */
.editor-backdrop {
  position: fixed; inset: 0; z-index: 2000;
  background: rgba(0,0,0,0.5); backdrop-filter: blur(4px);
  display: flex; align-items: center; justify-content: center;
  padding: 24px;
}
.editor-layout {
  display: flex; gap: 20px; max-width: 1240px; width: 100%;
  height: calc(100vh - 48px);
}

/* ===== 左面板 ===== */
.editor-main {
  flex: 1; max-width: 800px; background: #fff; border-radius: 16px;
  box-shadow: 0 20px 60px rgba(0,0,0,0.08);
  display: flex; flex-direction: column; overflow: hidden;
}

/* 标题 */
.title-area { padding: 28px 40px 12px; }
.title-input {
  width: 100%; border: none; outline: none;
  font-size: 18px; font-weight: 600; letter-spacing: -0.01em;
  color: #191c1d; padding: 0;
  border-bottom: 2px solid transparent;
  transition: border-color 0.2s;
}
.title-input:focus { border-bottom-color: #409eff; }
.title-input::placeholder { color: #c0c7d4; }
.ai-pill {
  display: inline-flex; align-items: center; gap: 4px;
  margin-top: 8px; padding: 4px 12px;
  background: #dee2ed; color: #5a5f68; border: none; border-radius: 999px;
  font-size: 12px; font-weight: 600; cursor: pointer;
  transition: background 0.2s;
}
.ai-pill:hover { background: #d3e4ff; color: #0060a9; }
.ai-pill.working {
  background: linear-gradient(90deg, #d3e4ff 25%, #a2c9ff 50%, #d3e4ff 75%);
  background-size: 200% 100%; animation: shimmer 1.5s infinite linear;
  color: #0060a9; pointer-events: none;
}

/* 元信息 */
.meta-section { padding: 0 40px; margin-top: 8px; }
.meta-details { background: #f3f4f5; border-radius: 10px; }
.meta-summary {
  display: flex; justify-content: space-between; align-items: center;
  padding: 10px 14px; cursor: pointer; user-select: none;
  font-size: 13px; font-weight: 500; color: #5a5f68;
  list-style: none;
}
.meta-summary::-webkit-details-marker { display: none; }
.chevron { transition: transform 0.2s; font-size: 14px; }
details[open] .chevron { transform: rotate(180deg); }
.meta-body { padding: 4px 14px 14px; }
.meta-field { margin-bottom: 14px; }
.meta-field label {
  display: block; font-size: 10px; font-weight: 700;
  text-transform: uppercase; letter-spacing: 0.08em;
  color: #5a5f68; margin-bottom: 5px;
}
.field-with-ai { display: flex; gap: 6px; }
.field-with-ai input, .field-with-ai textarea {
  flex: 1; padding: 7px 10px; border: 1px solid #e1e3e4; border-radius: 8px;
  font-size: 13px; outline: none; background: #fff;
  transition: border-color 0.2s;
}
.field-with-ai input:focus, .field-with-ai textarea:focus { border-color: #409eff; }
.mono-input { font-family: 'SF Mono', Monaco, Menlo, monospace !important; }
.ai-mini {
  width: 34px; flex-shrink: 0; border: 1px solid #e1e3e4; border-radius: 8px;
  background: #fff; cursor: pointer; font-size: 14px;
  display: flex; align-items: center; justify-content: center;
  transition: all 0.2s;
}
.ai-mini:hover { border-color: #409eff; background: #f0f4ff; }
.ai-mini.working {
  border-color: #409eff; background: linear-gradient(90deg, #f0f4ff 25%, #d3e4ff 50%, #f0f4ff 75%);
  background-size: 200% 100%; animation: shimmer 1.5s infinite linear;
  pointer-events: none;
}
.spin-icon { display: inline-block; animation: spin 0.8s linear infinite; }
@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }

/* 编辑器 */
.editor-area { flex: 1; min-height: 300px; padding: 0 40px; margin-top: 16px; overflow: hidden; display: flex; flex-direction: column; }
.editor-area :deep(.rich-editor) { flex: 1; min-height: 300px; }

/* 底栏 */
.bottom-bar {
  padding: 12px 40px; border-top: 1px solid #e7e8e9;
  display: flex; justify-content: space-between; align-items: center;
  flex-shrink: 0; background: #fff;
}
.bar-left, .bar-right { display: flex; align-items: center; gap: 14px; }
.status-pills {
  display: flex; background: #edeeef; border-radius: 999px; padding: 3px;
}
.status-pills button {
  padding: 4px 14px; border: none; border-radius: 999px; font-size: 12px; font-weight: 600;
  cursor: pointer; background: transparent; color: #707784; transition: all 0.2s;
}
.status-pills button.active { background: #fff; color: #191c1d; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
.sort-field { display: flex; align-items: center; gap: 6px; font-size: 12px; color: #5a5f68; }
.sort-field input {
  width: 48px; height: 28px; text-align: center; border: none; border-radius: 6px;
  background: #edeeef; font-size: 12px; outline: none;
}
.sort-field input:focus { background: #e0eaff; }
.pin-toggle {
  display: flex; align-items: center; gap: 6px; cursor: pointer;
  font-size: 12px; font-weight: 600; color: #5a5f68;
}
.pin-toggle input { display: none; }
.toggle-track {
  width: 32px; height: 16px; background: #dee2ed; border-radius: 999px;
  position: relative; transition: background 0.2s;
}
.pin-toggle input:checked ~ .toggle-track { background: #409eff; }
.toggle-thumb {
  width: 12px; height: 12px; background: #fff; border-radius: 50%;
  position: absolute; top: 2px; left: 2px; transition: transform 0.2s;
}
.pin-toggle input:checked ~ .toggle-track .toggle-thumb { transform: translateX(16px); }
.btn-cancel {
  padding: 6px 16px; border: 1px solid #e1e3e4; border-radius: 8px;
  background: #fff; font-size: 13px; cursor: pointer; color: #5a5f68;
}
.btn-cancel:hover { background: #f3f4f5; }
.btn-save {
  padding: 6px 20px; border: none; border-radius: 8px;
  background: #409eff; color: #fff; font-size: 13px; font-weight: 600;
  cursor: pointer; transition: all 0.2s;
}
.btn-save:hover:not(:disabled) { background: #3490e8; }
.btn-save.loading { opacity: 0.7; pointer-events: none; }
.btn-save:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-save.btn-draft {
  background: #f3f4f5; color: #5a5f68; border: 1px solid #e1e3e4;
}
.btn-save.btn-draft:hover:not(:disabled) { background: #e8ebf0; }
.btn-save.btn-schedule {
  background: #fff; color: #e67e22; border: 1px solid #f5c78e;
}
.btn-save.btn-schedule:hover:not(:disabled) { background: #fff7ec; }

.status-indicator {
  padding: 4px 12px; border-radius: 999px; font-size: 12px; font-weight: 600;
  border: 1px solid transparent;
  white-space: nowrap;
}
.status-indicator.s-draft { background: #f3f4f5; color: #5a5f68; }
.status-indicator.s-live { background: #e7f7ee; color: #1fa25a; }
.status-indicator.s-scheduled { background: #fff7ec; color: #e67e22; border-color: #f5c78e; }
.status-indicator.clickable { cursor: pointer; transition: filter 0.15s; }
.status-indicator.clickable:hover { filter: brightness(0.96); }

/* ===== 定时发布 Dialog ===== */
.schedule-modal {
  position: fixed; inset: 0; z-index: 2100;
  background: rgba(0,0,0,0.4); backdrop-filter: blur(2px);
  display: flex; align-items: center; justify-content: center;
  animation: fadeIn 0.15s ease-out;
}
.schedule-card {
  background: #fff; border-radius: 14px; box-shadow: 0 24px 60px rgba(0,0,0,0.18);
  width: 420px; max-width: calc(100vw - 32px); padding: 24px;
  display: flex; flex-direction: column; gap: 14px;
}
.schedule-title { font-size: 16px; font-weight: 600; color: #191c1d; }
.schedule-hint { font-size: 13px; color: #5a5f68; line-height: 1.6; }
.schedule-input {
  width: 100%; height: 40px; padding: 0 12px;
  border: 1px solid #e1e3e4; border-radius: 8px;
  font-size: 14px; font-family: inherit; color: #191c1d;
  outline: none; transition: border-color 0.2s;
  color-scheme: light;
}
.schedule-input:focus { border-color: #409eff; }
.schedule-preview {
  font-size: 13px; color: #0060a9; background: #e8f3ff;
  padding: 8px 12px; border-radius: 8px;
}
.schedule-footer {
  display: flex; justify-content: space-between; align-items: center;
  gap: 8px; margin-top: 4px;
}
.schedule-footer-right { display: flex; gap: 8px; }
.btn-danger-text { color: #d9534f; border-color: #f5c1bf; }
.btn-danger-text:hover { background: #fef2f2; }
@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }

/* ===== 右面板 ===== */
.ai-aside {
  width: 380px; flex-shrink: 0; background: #fff; border-radius: 16px;
  box-shadow: 0 20px 60px rgba(0,0,0,0.08);
  display: flex; flex-direction: column; overflow: hidden;
}
.ai-header {
  padding: 14px 20px;
  background: linear-gradient(135deg, #409eff, #004881);
  color: #fff; font-size: 14px; font-weight: 600; letter-spacing: 0.02em;
  flex-shrink: 0;
}
.ai-scroll { flex: 1; overflow-y: auto; padding: 20px; min-height: 0; }
.ai-zone { margin-bottom: 20px; }
.zone-label {
  display: flex; align-items: center; gap: 6px;
  font-size: 11px; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.1em; color: #5a5f68; margin-bottom: 8px;
}
.dot { width: 4px; height: 4px; border-radius: 50%; background: #409eff; }
.kw-row { display: flex; gap: 8px; align-items: center; }
.kw-row select, .kw-row input {
  flex: 1; min-width: 0;
  padding: 8px 10px; border: none; border-radius: 10px;
  background: #f3f4f5; font-size: 12px; outline: none;
  transition: all 0.2s; box-sizing: border-box;
}
.kw-row select:focus, .kw-row input:focus { background: #e0eaff; }
.tag-empty { padding: 12px; text-align: center; color: #94a3b8; font-size: 12px; }

/* el-select trigger 对齐右侧 input 的灰底圆角风格 */
.kw-row :deep(.el-select) { flex: 0 0 40%; }
.kw-row :deep(.el-select .el-select__wrapper) {
  min-height: 32px;
  padding: 4px 10px;
  border-radius: 10px;
  background: #f3f4f5;
  box-shadow: none !important;
  font-size: 12px;
  transition: background 0.2s;
}
.kw-row :deep(.el-select .el-select__wrapper:hover),
.kw-row :deep(.el-select.is-focused .el-select__wrapper) {
  background: #e0eaff;
  box-shadow: none !important;
}
.kw-row :deep(.el-select .el-select__placeholder) { color: #9ca3af; }
.kw-row :deep(.el-select .el-select__selected-item) { color: #1f2937; }

/* 生成按钮 */
.gen-btn {
  width: 100%; padding: 14px; border: none; border-radius: 12px;
  background: linear-gradient(135deg, #409eff, #3490e8);
  color: #fff; font-size: 14px; font-weight: 700;
  cursor: pointer; transition: all 0.2s;
  box-shadow: 0 6px 20px rgba(64,158,255,0.25);
}
.gen-btn:hover:not(:disabled):not(.working) { transform: scale(0.98); }
.gen-btn:disabled:not(.working) { opacity: 0.5; cursor: default; }
.gen-btn.working {
  background: linear-gradient(135deg, #409eff, #66b1ff, #409eff);
  background-size: 200% 200%; animation: gradient-shift 2s ease infinite;
  cursor: wait;
}
.btn-dots { display: inline-flex; gap: 3px; margin-right: 8px; }
.btn-dots i {
  width: 6px; height: 6px; border-radius: 50%; background: rgba(255,255,255,0.7);
  display: block; animation: bounce 1.4s infinite ease-in-out both;
}
.btn-dots i:nth-child(1) { animation-delay: -0.32s; }
.btn-dots i:nth-child(2) { animation-delay: -0.16s; }
@keyframes gradient-shift {
  0% { background-position: 0% 50%; }
  50% { background-position: 100% 50%; }
  100% { background-position: 0% 50%; }
}
@keyframes bounce {
  0%,80%,100% { transform: scale(0.4); opacity: 0.3; }
  40% { transform: scale(1); opacity: 1; }
}

/* Shimmer */
.shimmer-bar {
  margin-top: 10px; padding: 12px 14px; border-radius: 12px;
  background: linear-gradient(90deg, #f0f4ff 25%, #e0eaff 50%, #f0f4ff 75%);
  background-size: 200% 100%;
  animation: shimmer 2s infinite linear;
  display: flex; align-items: center; gap: 8px;
  font-size: 12px; font-weight: 500; color: #409eff;
}
.shimmer-icon { animation: pulse 1s infinite; }
@keyframes shimmer { from { background-position: 200% 0; } to { background-position: -200% 0; } }
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }

/* 结果卡 */
.result-card {
  background: rgba(222,226,237,0.3); border: 1px solid rgba(64,158,255,0.1);
  border-radius: 12px; padding: 14px;
}
.result-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
.chip-primary {
  padding: 4px 10px; border: none; border-radius: 6px;
  background: #409eff; color: #fff; font-size: 11px; font-weight: 700;
  cursor: pointer;
}
.chip-primary:hover { background: #3490e8; }
.chip-outline {
  padding: 4px 10px; border: 1px solid #e1e3e4; border-radius: 6px;
  background: #fff; color: #191c1d; font-size: 11px; font-weight: 700;
  cursor: pointer;
}
.chip-outline:hover { background: #f3f4f5; }
.chip-icon {
  margin-left: auto; background: none; border: none; cursor: pointer;
  font-size: 14px; opacity: 0.6;
}
.chip-icon:hover { opacity: 1; }

.result-content {
  overflow-y: auto; font-size: 13px; line-height: 1.75; color: #404752;
}
.result-content :deep(h2) {
  font-size: 13px; font-weight: 700; border-left: 3px solid #409eff;
  padding-left: 10px; margin: 12px 0 6px;
}
.result-content :deep(p) { margin: 4px 0; }
.result-content :deep(ul), .result-content :deep(ol) { padding-left: 16px; margin: 4px 0; }
.result-content :deep(blockquote) {
  border: none; padding: 10px 12px; margin: 8px 0;
  background: rgba(255,255,255,0.5); border-radius: 8px;
  position: relative; overflow: hidden; font-size: 12px; font-style: italic;
}
.result-content :deep(blockquote)::before {
  content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 3px;
  background: linear-gradient(to bottom, #409eff, #004881);
}
.result-content :deep(code) {
  background: #2e3132; color: #f0f1f2; padding: 2px 6px; border-radius: 4px; font-size: 11px;
}
.result-content :deep(pre) {
  background: #2e3132; color: #f0f1f2; padding: 10px; border-radius: 8px;
  font-size: 11px; overflow-x: auto;
}
.result-content :deep(strong) { font-weight: 600; }

/* 聊天栏 */
.chat-bar {
  padding: 12px 16px; border-top: 1px solid #e7e8e9; background: #fff; flex-shrink: 0;
}
.chat-input-wrap {
  display: flex; align-items: center; gap: 8px;
  background: #f3f4f5; border-radius: 999px; padding: 6px 6px 6px 16px;
  transition: all 0.2s;
}
.chat-input-wrap:focus-within { background: #fff; box-shadow: 0 0 0 2px rgba(64,158,255,0.2); }
.chat-input-wrap input {
  flex: 1; border: none; background: transparent; outline: none;
  font-size: 12px; color: #191c1d;
}
.send-btn {
  width: 32px; height: 32px; border: none; border-radius: 50%;
  background: #409eff; color: #fff; cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  font-size: 14px; transition: transform 0.15s;
}
.send-btn:hover { transform: scale(1.05); }
.send-btn:disabled { opacity: 0.5; }
</style>

<!-- 弹出层样式 — el-select popper teleport 到 body，需非 scoped -->
<style>
.ai-kw-popper.el-popper {
  border-radius: 10px !important;
  border: 1px solid var(--el-border-color-lighter) !important;
  box-shadow: 0 12px 32px -8px rgba(0, 0, 0, 0.18), 0 4px 10px -2px rgba(0, 0, 0, 0.08) !important;
  overflow: hidden;
  padding: 4px !important;
}
.ai-kw-popper .el-select-dropdown__list {
  padding: 0 !important;
}
.ai-kw-popper .el-select-dropdown__item {
  height: 34px;
  line-height: 34px;
  padding: 0 12px;
  margin: 2px 0;
  border-radius: 6px;
  font-size: 13px;
  color: var(--el-text-color-regular);
  transition: background 0.12s, color 0.12s;
}
.ai-kw-popper .el-select-dropdown__item:hover,
.ai-kw-popper .el-select-dropdown__item.hover {
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
}
.ai-kw-popper .el-select-dropdown__item.selected {
  background: var(--el-color-primary);
  color: #fff;
  font-weight: 500;
}
.ai-kw-popper .el-select-dropdown__item.selected:hover {
  background: var(--el-color-primary-dark-2);
  color: #fff;
}
.ai-kw-popper .el-select-dropdown__empty,
.ai-kw-popper .el-select-dropdown__loading {
  padding: 14px 12px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
