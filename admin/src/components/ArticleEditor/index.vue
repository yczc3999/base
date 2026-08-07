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
            <div
class="status-indicator" :class="[`s-${statusKind}`, statusKind === 'scheduled' ? 'clickable' : '']"
              :title="statusKind === 'scheduled' ? '点击修改定时时间' : ''"
              @click="statusKind === 'scheduled' && openScheduleDialog()">
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
              <input v-model="form.is_pinned" type="checkbox" />
              <div class="toggle-track"><div class="toggle-thumb"></div></div>
            </label>
            <button class="btn-cancel" @click="$emit('close')">取消</button>
            <button class="btn-save btn-draft" :disabled="saving" @click="saveAs('draft')">存草稿</button>
            <button
class="btn-save btn-schedule" :disabled="saving"
              title="定时发布：点击选择时间" @click="openScheduleDialog">
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
            <button
v-if="form.scheduled_at" class="btn-cancel btn-danger-text"
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
              <el-select
v-model="topic.tagId" placeholder="搜索/选上线标签" size="small"
                filterable remote clearable
                :remote-method="searchTags" :loading="tagSearchLoading"
                popper-class="ai-kw-popper"
                style="flex:0 0 40%" @change="onTagSelect"
                @visible-change="onTagDropdownOpen">
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
          <div v-if="result" class="ai-zone">
            <div class="result-card">
              <div class="result-chips">
                <button class="chip-primary" @click="applyResult('replace')">✓ 用这篇</button>
                <button class="chip-outline" @click="generate">↻ 重写</button>
                <button class="chip-outline" @click="applyResult('append')">+ 追加</button>
                <button class="chip-icon" title="复制" @click="copy">📋</button>
              </div>
              <div class="result-content" v-html="resultHtml"></div>
            </div>
          </div>
        </div>

        <!-- 聊天输入 -->
        <div class="chat-bar">
          <div class="chat-input-wrap">
            <input
v-model="chatInput" placeholder="向 AI 提问或润色..."
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
import { ElMessage } from 'element-plus/es/components/message/index'
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
  background: var(--overlay-strong);
  display: flex; align-items: center; justify-content: center;
  padding: var(--space-xl);
}
.editor-layout {
  display: flex; gap: var(--space-lg); max-width: 1240px; width: 100%;
  height: calc(100vh - var(--space-3xl));
}

/* ===== 左面板 ===== */
.editor-main {
  flex: 1; max-width: 800px; background: var(--bg-card); border-radius: var(--radius);
  border: 1px solid var(--border);
  display: flex; flex-direction: column; overflow: hidden;
}

/* 标题 */
.title-area { padding: 28px var(--space-3xl) var(--space-md); }
.title-input {
  width: 100%; border: none; outline: none;
  font-size: 18px; font-weight: 600; letter-spacing: -0.01em;
  color: var(--text-primary); padding: 0;
  border-bottom: 2px solid transparent;
  transition: border-color 0.2s;
}
.title-input:focus { border-bottom-color: var(--primary); }
.title-input::placeholder { color: var(--text-placeholder); }
.ai-pill {
  display: inline-flex; align-items: center; gap: var(--space-xs);
  margin-top: var(--space-sm); padding: var(--space-xs) var(--space-md);
  background: var(--bg-subtle); color: var(--text-secondary); border: 1px solid var(--border); border-radius: var(--radius);
  font-size: 12px; font-weight: 600; cursor: pointer;
  transition: background 0.2s;
}
.ai-pill:hover { background: var(--primary-bg); color: var(--primary-hover); }
.ai-pill.working {
  background: var(--primary-bg);
  color: var(--primary-hover); pointer-events: none;
}

/* 元信息 */
.meta-section { padding: 0 var(--space-3xl); margin-top: var(--space-sm); }
.meta-details { background: var(--bg-subtle); border: 1px solid var(--border); border-radius: var(--radius); }
.meta-summary {
  display: flex; justify-content: space-between; align-items: center;
  padding: var(--space-sm) var(--space-md); cursor: pointer; user-select: none;
  font-size: var(--text-sm); font-weight: 500; color: var(--text-secondary);
  list-style: none;
}
.meta-summary::-webkit-details-marker { display: none; }
.chevron { transition: transform 0.2s; font-size: var(--text-base); }
details[open] .chevron { transform: rotate(180deg); }
.meta-body { padding: var(--space-xs) var(--space-md) var(--space-md); }
.meta-field { margin-bottom: var(--space-md); }
.meta-field label {
  display: block; font-size: var(--text-xs); font-weight: 700;
  text-transform: uppercase; letter-spacing: 0.08em;
  color: var(--text-secondary); margin-bottom: var(--space-xs);
}
.field-with-ai { display: flex; gap: var(--space-xs); }
.field-with-ai input, .field-with-ai textarea {
  flex: 1; padding: 7px var(--space-sm); border: 1px solid var(--border); border-radius: var(--radius);
  font-size: var(--text-sm); outline: none; background: var(--bg-card);
  transition: border-color 0.2s;
}
.field-with-ai input:focus, .field-with-ai textarea:focus { border-color: var(--primary); }
.mono-input { font-family: 'SF Mono', Monaco, Menlo, monospace !important; }
.ai-mini {
  width: 34px; flex-shrink: 0; border: 1px solid var(--border); border-radius: var(--radius);
  background: var(--bg-card); cursor: pointer; font-size: var(--text-base);
  display: flex; align-items: center; justify-content: center;
  transition: background 0.2s;
}
.ai-mini:hover { border-color: var(--primary); background: var(--primary-bg); }
.ai-mini.working {
  border-color: var(--primary); background: var(--primary-bg);
  pointer-events: none;
}
.spin-icon { display: inline-block; animation: spin 0.8s linear infinite; }
@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }

/* 编辑器 */
.editor-area { flex: 1; min-height: 300px; padding: 0 var(--space-3xl); margin-top: var(--space-base); overflow: hidden; display: flex; flex-direction: column; }
.editor-area :deep(.rich-editor) { flex: 1; min-height: 300px; }

/* 底栏 */
.bottom-bar {
  padding: var(--space-md) var(--space-3xl); border-top: 1px solid var(--border);
  display: flex; justify-content: space-between; align-items: center;
  flex-shrink: 0; background: var(--bg-card);
}
.bar-left, .bar-right { display: flex; align-items: center; gap: var(--space-md); }
.status-pills {
  display: flex; background: var(--bg-subtle); border: 1px solid var(--border); border-radius: var(--radius); padding: 3px;
}
.status-pills button {
  padding: var(--space-xs) var(--space-md); border: none; border-radius: var(--radius); font-size: var(--text-xs); font-weight: 600;
  cursor: pointer; background: transparent; color: var(--text-secondary); transition: background 0.2s;
}
.status-pills button.active { background: var(--bg-card); color: var(--text-primary); border: 1px solid var(--border); }
.sort-field { display: flex; align-items: center; gap: var(--space-xs); font-size: var(--text-xs); color: var(--text-secondary); }
.sort-field input {
  width: 48px; height: 28px; text-align: center; border: 1px solid var(--border); border-radius: var(--radius);
  background: var(--bg-subtle); font-size: var(--text-xs); outline: none;
}
.sort-field input:focus { border-color: var(--primary); background: var(--bg-card); }
.pin-toggle {
  display: flex; align-items: center; gap: var(--space-xs); cursor: pointer;
  font-size: var(--text-xs); font-weight: 600; color: var(--text-secondary);
}
.pin-toggle input { display: none; }
.toggle-track {
  width: 32px; height: 16px; background: var(--border-dark); border-radius: var(--radius);
  position: relative; transition: background 0.2s;
}
.pin-toggle input:checked ~ .toggle-track { background: var(--primary); }
.toggle-thumb {
  width: 12px; height: 12px; background: var(--bg-card); border-radius: 50%;
  position: absolute; top: 2px; left: 2px; transition: transform 0.2s;
}
.pin-toggle input:checked ~ .toggle-track .toggle-thumb { transform: translateX(16px); }
.btn-cancel {
  padding: var(--space-xs) var(--space-base); border: 1px solid var(--border); border-radius: var(--radius);
  background: var(--bg-card); font-size: var(--text-sm); cursor: pointer; color: var(--text-secondary);
}
.btn-cancel:hover { background: var(--bg-subtle); }
.btn-save {
  padding: var(--space-xs) var(--space-lg); border: none; border-radius: var(--radius);
  background: var(--primary); color: var(--text-inverse); font-size: var(--text-sm); font-weight: 600;
  cursor: pointer; transition: background 0.2s;
}
.btn-save:hover:not(:disabled) { background: var(--primary-hover); }
.btn-save.loading { opacity: 0.7; pointer-events: none; }
.btn-save:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-save.btn-draft {
  background: var(--bg-subtle); color: var(--text-secondary); border: 1px solid var(--border);
}
.btn-save.btn-draft:hover:not(:disabled) { background: var(--bg-page); }
.btn-save.btn-schedule {
  background: var(--bg-card); color: var(--warning); border: 1px solid var(--warning-bg);
}
.btn-save.btn-schedule:hover:not(:disabled) { background: var(--warning-bg); }

.status-indicator {
  padding: var(--space-xs) var(--space-md); border-radius: var(--radius); font-size: var(--text-xs); font-weight: 600;
  border: 1px solid transparent;
  white-space: nowrap;
}
.status-indicator.s-draft { background: var(--bg-subtle); color: var(--text-secondary); }
.status-indicator.s-live { background: var(--success-bg); color: var(--success); }
.status-indicator.s-scheduled { background: var(--warning-bg); color: var(--warning); border-color: var(--warning-bg); }
.status-indicator.clickable { cursor: pointer; transition: filter 0.15s; }
.status-indicator.clickable:hover { filter: brightness(0.96); }

/* ===== 定时发布 Dialog ===== */
.schedule-modal {
  position: fixed; inset: 0; z-index: 2100;
  background: var(--overlay-strong);
  display: flex; align-items: center; justify-content: center;
  animation: fadeIn 0.15s ease-out;
}
.schedule-card {
  background: var(--bg-card); border: 1px solid var(--border); border-radius: var(--radius); box-shadow: none;
  width: 420px; max-width: calc(100vw - 32px); padding: var(--space-xl);
  display: flex; flex-direction: column; gap: var(--space-md);
}
.schedule-title { font-size: var(--text-lg); font-weight: 600; color: var(--text-primary); }
.schedule-hint { font-size: var(--text-sm); color: var(--text-secondary); line-height: 1.6; }
.schedule-input {
  width: 100%; height: 40px; padding: 0 var(--space-md);
  border: 1px solid var(--border); border-radius: var(--radius);
  font-size: var(--text-base); font-family: inherit; color: var(--text-primary);
  outline: none; transition: border-color 0.2s;
  color-scheme: light;
}
.schedule-input:focus { border-color: var(--primary); }
.schedule-preview {
  font-size: var(--text-sm); color: var(--primary-hover); background: var(--primary-bg);
  padding: var(--space-sm) var(--space-md); border-radius: var(--radius);
}
.schedule-footer {
  display: flex; justify-content: space-between; align-items: center;
  gap: var(--space-sm); margin-top: var(--space-xs);
}
.schedule-footer-right { display: flex; gap: var(--space-sm); }
.btn-danger-text { color: var(--danger); border-color: var(--danger-bg); }
.btn-danger-text:hover { background: var(--danger-bg); }
@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }

/* ===== 右面板 ===== */
.ai-aside {
  width: 380px; flex-shrink: 0; background: var(--bg-card); border: 1px solid var(--border); border-radius: var(--radius);
  box-shadow: none;
  display: flex; flex-direction: column; overflow: hidden;
}
.ai-header {
  padding: var(--space-md) var(--space-lg);
  background: var(--primary);
  color: var(--text-inverse); font-size: var(--text-base); font-weight: 600; letter-spacing: 0.02em;
  flex-shrink: 0;
}
.ai-scroll { flex: 1; overflow-y: auto; padding: var(--space-lg); min-height: 0; }
.ai-zone { margin-bottom: var(--space-lg); }
.zone-label {
  display: flex; align-items: center; gap: var(--space-xs);
  font-size: var(--text-xs); font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.1em; color: var(--text-secondary); margin-bottom: var(--space-sm);
}
.dot { width: 4px; height: 4px; border-radius: 50%; background: var(--primary); }
.kw-row { display: flex; gap: var(--space-sm); align-items: center; }
.kw-row select, .kw-row input {
  flex: 1; min-width: 0;
  padding: var(--space-sm) var(--space-sm); border: 1px solid var(--border); border-radius: var(--radius);
  background: var(--bg-subtle); font-size: var(--text-xs); outline: none;
  transition: border-color 0.2s; box-sizing: border-box;
}
.kw-row select:focus, .kw-row input:focus { border-color: var(--primary); background: var(--bg-card); }
.tag-empty { padding: var(--space-md); text-align: center; color: var(--text-placeholder); font-size: var(--text-xs); }

/* el-select trigger 对齐右侧 input 的灰底圆角风格 */
.kw-row :deep(.el-select) { flex: 0 0 40%; }
.kw-row :deep(.el-select .el-select__wrapper) {
  min-height: 32px;
  padding: var(--space-xs) var(--space-sm);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg-subtle);
  box-shadow: none !important;
  font-size: var(--text-xs);
  transition: border-color 0.2s, background 0.2s;
}
.kw-row :deep(.el-select .el-select__wrapper:hover),
.kw-row :deep(.el-select.is-focused .el-select__wrapper) {
  border-color: var(--primary);
  background: var(--bg-card);
  box-shadow: none !important;
}
.kw-row :deep(.el-select .el-select__placeholder) { color: var(--text-placeholder); }
.kw-row :deep(.el-select .el-select__selected-item) { color: var(--text-primary); }

/* 生成按钮 */
.gen-btn {
  width: 100%; padding: var(--space-md); border: none; border-radius: var(--radius);
  background: var(--primary);
  color: var(--text-inverse); font-size: var(--text-base); font-weight: 700;
  cursor: pointer; transition: background 0.2s;
  box-shadow: none;
}
.gen-btn:hover:not(:disabled):not(.working) { background: var(--primary-hover); }
.gen-btn:disabled:not(.working) { opacity: 0.5; cursor: default; }
.gen-btn.working {
  background: var(--primary-hover);
  cursor: wait;
}
.btn-dots { display: inline-flex; gap: 3px; margin-right: var(--space-sm); }
.btn-dots i {
  width: 6px; height: 6px; border-radius: 50%; background: var(--text-inverse);
  display: block; animation: bounce 1.4s infinite ease-in-out both;
  opacity: 0.7;
}
.btn-dots i:nth-child(1) { animation-delay: -0.32s; }
.btn-dots i:nth-child(2) { animation-delay: -0.16s; }
@keyframes bounce {
  0%,80%,100% { opacity: 0.3; }
  40% { opacity: 1; }
}

/* Shimmer（纯色块替代，去除渐变） */
.shimmer-bar {
  margin-top: var(--space-sm); padding: var(--space-md) var(--space-md); border: 1px solid var(--border); border-radius: var(--radius);
  background: var(--primary-bg);
  display: flex; align-items: center; gap: var(--space-sm);
  font-size: var(--text-xs); font-weight: 500; color: var(--primary-hover);
}
.shimmer-icon { animation: pulse 1s infinite; }
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }

/* 结果卡 */
.result-card {
  background: var(--bg-page); border: 1px solid var(--border);
  border-radius: var(--radius); padding: var(--space-md);
}
.result-chips { display: flex; flex-wrap: wrap; gap: var(--space-xs); margin-bottom: var(--space-sm); }
.chip-primary {
  padding: var(--space-xs) var(--space-sm); border: none; border-radius: var(--radius);
  background: var(--primary); color: var(--text-inverse); font-size: var(--text-xs); font-weight: 700;
  cursor: pointer;
}
.chip-primary:hover { background: var(--primary-hover); }
.chip-outline {
  padding: var(--space-xs) var(--space-sm); border: 1px solid var(--border); border-radius: var(--radius);
  background: var(--bg-card); color: var(--text-primary); font-size: var(--text-xs); font-weight: 700;
  cursor: pointer;
}
.chip-outline:hover { background: var(--bg-subtle); }
.chip-icon {
  margin-left: auto; background: none; border: none; cursor: pointer;
  font-size: var(--text-base); opacity: 0.6;
}
.chip-icon:hover { opacity: 1; }

.result-content {
  overflow-y: auto; font-size: var(--text-sm); line-height: 1.75; color: var(--text-primary);
}
.result-content :deep(h2) {
  font-size: var(--text-sm); font-weight: 700; border-left: 3px solid var(--primary);
  padding-left: var(--space-sm); margin: var(--space-md) 0 var(--space-xs);
}
.result-content :deep(p) { margin: var(--space-xs) 0; }
.result-content :deep(ul), .result-content :deep(ol) { padding-left: var(--space-base); margin: var(--space-xs) 0; }
.result-content :deep(blockquote) {
  border: 1px solid var(--border); padding: var(--space-sm) var(--space-md); margin: var(--space-sm) 0;
  background: var(--bg-subtle); border-radius: var(--radius);
  position: relative; overflow: hidden; font-size: var(--text-xs); font-style: italic;
}
.result-content :deep(blockquote)::before {
  content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 3px;
  background: var(--primary);
}
.result-content :deep(code) {
  background: #2e3132; color: #f0f1f2; padding: var(--space-xs) var(--space-xs); border-radius: 4px; font-size: var(--text-xs);
}
.result-content :deep(pre) {
  background: #2e3132; color: #f0f1f2; padding: var(--space-sm); border-radius: var(--radius);
  font-size: var(--text-xs); overflow-x: auto;
}
.result-content :deep(strong) { font-weight: 600; }

/* 聊天栏 */
.chat-bar {
  padding: var(--space-md) var(--space-base); border-top: 1px solid var(--border); background: var(--bg-card); flex-shrink: 0;
}
.chat-input-wrap {
  display: flex; align-items: center; gap: var(--space-sm);
  background: var(--bg-subtle); border: 1px solid var(--border); border-radius: var(--radius); padding: var(--space-xs) var(--space-xs) var(--space-xs) var(--space-base);
  transition: border-color 0.2s;
}
.chat-input-wrap:focus-within { border-color: var(--primary); background: var(--bg-card); }
.chat-input-wrap input {
  flex: 1; border: none; background: transparent; outline: none;
  font-size: var(--text-xs); color: var(--text-primary);
}
.send-btn {
  width: 32px; height: 32px; border: none; border-radius: 50%;
  background: var(--primary); color: var(--text-inverse); cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  font-size: var(--text-base); transition: background 0.15s;
}
.send-btn:hover { background: var(--primary-hover); }
.send-btn:disabled { opacity: 0.5; }
</style>

<!-- 弹出层样式 — el-select popper teleport 到 body，需非 scoped -->
<style>
.ai-kw-popper.el-popper {
  border-radius: var(--radius) !important;
  border: 1px solid var(--border) !important;
  box-shadow: none !important;
  overflow: hidden;
  padding: var(--space-xs) !important;
}
.ai-kw-popper .el-select-dropdown__list {
  padding: 0 !important;
}
.ai-kw-popper .el-select-dropdown__item {
  height: 34px;
  line-height: 34px;
  padding: 0 var(--space-md);
  margin: 2px 0;
  border-radius: var(--radius);
  font-size: var(--text-sm);
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
  color: var(--text-inverse);
  font-weight: 500;
}
.ai-kw-popper .el-select-dropdown__item.selected:hover {
  background: var(--el-color-primary-dark-2);
  color: var(--text-inverse);
}
.ai-kw-popper .el-select-dropdown__empty,
.ai-kw-popper .el-select-dropdown__loading {
  padding: 14px 12px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
