<template>
  <div class="article-edit" v-loading="loading">
    <div class="edit-header">
      <h2>{{ isEdit ? '编辑文章' : '新建文章' }}</h2>
      <div class="header-actions">
        <el-button @click="$router.back()">返回</el-button>
        <el-tag v-if="statusKind === 'scheduled'" type="warning" effect="light">
          ⏰ 定时：{{ form.published_at }}
        </el-tag>
        <el-tag v-else-if="statusKind === 'live'" type="success" effect="light">✅ 已发布</el-tag>
        <el-tag v-else type="info" effect="light">📝 草稿</el-tag>
        <el-button @click="saveAs('draft')" :loading="saving">存草稿</el-button>
        <el-button @click="saveAs('schedule')" :loading="saving" :disabled="!isFutureScheduled"
          :title="isFutureScheduled ? '' : '先在右侧填一个未来的发布时间'">定时发布</el-button>
        <el-button type="primary" @click="saveAs('publish')" :loading="saving">立即发布</el-button>
      </div>
    </div>

    <div class="edit-body">
      <div class="edit-main">
        <!-- 标题 -->
        <div class="title-row">
          <el-input v-model="form.title" placeholder="输入文章标题" class="title-input" size="large" />
          <el-button :icon="MagicStick" :loading="aiTitle" @click="generateTitle" circle title="AI 生成标题" />
        </div>

        <!-- 富文本编辑器 -->
        <div class="editor-wrap">
          <Toolbar :editor="editorRef" :default-config="toolbarConfig" mode="default" />
          <Editor v-model="form.content" :default-config="editorConfig" mode="default"
            style="min-height: 500px" @onCreated="onEditorCreated" />
        </div>
      </div>

      <div class="edit-sidebar">
        <!-- URL 别名 -->
        <div class="sidebar-card">
          <div class="card-title">URL 别名</div>
          <div class="slug-row">
            <el-input v-model="form.slug" placeholder="如 bilibili-guide" />
            <el-button :icon="MagicStick" :loading="aiSlug" @click="generateSlug" circle size="small" title="AI 生成" />
          </div>
        </div>

        <!-- 摘要 -->
        <div class="sidebar-card">
          <div class="card-title">
            摘要
            <el-button :icon="MagicStick" :loading="aiSummary" @click="generateSummary" text size="small">AI 提取</el-button>
          </div>
          <el-input v-model="form.summary" type="textarea" :rows="4" placeholder="文章简介" />
        </div>

        <!-- 封面图 -->
        <div class="sidebar-card">
          <div class="card-title">封面图</div>
          <el-input v-model="form.cover_image" placeholder="图片 URL" />
        </div>

        <!-- 设置 -->
        <div class="sidebar-card">
          <div class="card-title">设置</div>
          <el-form label-width="60px" size="small">
            <el-form-item label="排序">
              <el-input-number v-model="form.sort" :min="0" :max="999" />
            </el-form-item>
            <el-form-item label="置顶">
              <el-switch v-model="form.is_pinned" />
            </el-form-item>
            <el-form-item label="发布时间">
              <el-date-picker v-model="form.published_at" type="datetime" size="small"
                placeholder="默认=发布时自动填" value-format="YYYY-MM-DD HH:mm:ss" style="width:100%" />
            </el-form-item>
          </el-form>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, shallowRef, onBeforeUnmount, onMounted, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { MagicStick } from '@element-plus/icons-vue'
import { Editor, Toolbar } from '@wangeditor/editor-for-vue'
import { get, post } from '@/api/request'
import '@wangeditor/editor/dist/css/style.css'

const route = useRoute()
const router = useRouter()
const isEdit = computed(() => !!route.query.id)
const loading = ref(false)
const saving = ref(false)

const form = reactive({
  id: null as number | null,
  title: '',
  slug: '',
  summary: '',
  content: '',
  cover_image: '',
  is_pinned: false,
  sort: 0,
  status: 0,
  published_at: null as string | null,
})

// ---- 编辑器 ----

const editorRef = shallowRef()
const toolbarConfig = {}
const editorConfig = { placeholder: '写点什么...' }
function onEditorCreated(editor: any) { editorRef.value = editor }
onBeforeUnmount(() => { editorRef.value?.destroy() })

// ---- 加载 ----

onMounted(async () => {
  const id = route.query.id
  if (id) {
    loading.value = true
    try {
      const data = await get(`/admin/article/detail`, { id })
      Object.assign(form, data)
    } finally {
      loading.value = false
    }
  }
})

// ---- 保存 ----

const isFutureScheduled = computed(() => {
  if (!form.published_at) return false
  return new Date(String(form.published_at).replace(' ', 'T')).getTime() > Date.now()
})
const statusKind = computed<'draft' | 'scheduled' | 'live'>(() => {
  if (+form.status === 0) return 'draft'
  return isFutureScheduled.value ? 'scheduled' : 'live'
})

async function saveAs(action: 'draft' | 'publish' | 'schedule') {
  if (action !== 'draft') {
    if (!form.title) return ElMessage.warning('请输入标题')
    if (!form.content) return ElMessage.warning('请输入正文')
  }
  if (action === 'draft') {
    form.status = 0
  } else if (action === 'publish') {
    form.status = 1
    form.published_at = null  // 清空让后端钩子填 NOW
  } else if (action === 'schedule') {
    if (!isFutureScheduled.value) return ElMessage.warning('请先填一个未来的发布时间')
    form.status = 1
  }
  await doSave(action)
}

async function doSave(action: 'draft' | 'publish' | 'schedule' = 'draft') {
  saving.value = true
  try {
    if (form.id) {
      await post('/admin/article/edit', form)
    } else {
      const res = await post('/admin/article/create', form)
      form.id = res?.id
      router.replace({ query: { id: form.id } })
    }
    const msg = action === 'draft' ? '已存为草稿'
      : action === 'publish' ? '已发布'
      : `已定时发布（${form.published_at}）`
    ElMessage.success(msg)
  } finally {
    saving.value = false
  }
}

// ---- AI 功能 ----

const aiTitle = ref(false)
const aiSlug = ref(false)
const aiSummary = ref(false)

async function generateTitle() {
  if (!form.content && !form.summary) return ElMessage.warning('先写点正文或摘要')
  aiTitle.value = true
  try {
    const res = await post('/admin/article/ai-generate', {
      type: 'title',
      content: (form.content || '').slice(0, 2000),
      summary: form.summary,
    })
    if (res?.result) form.title = res.result
  } catch (e: any) { ElMessage.error(e?.message || 'AI 失败') }
  finally { aiTitle.value = false }
}

async function generateSlug() {
  if (!form.title) return ElMessage.warning('先填标题')
  aiSlug.value = true
  try {
    const res = await post('/admin/article/ai-generate', {
      type: 'slug',
      title: form.title,
    })
    if (res?.result) form.slug = res.result
  } catch (e: any) { ElMessage.error(e?.message || 'AI 失败') }
  finally { aiSlug.value = false }
}

async function generateSummary() {
  if (!form.content) return ElMessage.warning('先写正文')
  aiSummary.value = true
  try {
    const res = await post('/admin/article/ai-generate', {
      type: 'summary',
      content: (form.content || '').slice(0, 3000),
      title: form.title,
    })
    if (res?.result) form.summary = res.result
  } catch (e: any) { ElMessage.error(e?.message || 'AI 失败') }
  finally { aiSummary.value = false }
}
</script>

<style scoped>
.article-edit { padding: 0 16px 24px; }
.edit-header {
  display: flex; justify-content: space-between; align-items: center;
  padding: 16px 0; border-bottom: 1px solid var(--el-border-color-lighter); margin-bottom: 16px;
}
.edit-header h2 { font-size: 18px; font-weight: 600; margin: 0; }
.header-actions { display: flex; gap: 8px; }
.edit-body { display: flex; gap: 20px; }
.edit-main { flex: 1; min-width: 0; }
.edit-sidebar { width: 300px; flex-shrink: 0; }
.title-row { display: flex; gap: 8px; margin-bottom: 12px; }
.title-input { flex: 1; }
.title-input :deep(.el-input__inner) { font-size: 20px; font-weight: 600; }
.editor-wrap {
  border: 1px solid var(--el-border-color); border-radius: 4px; overflow: hidden;
}
.editor-wrap :deep(.w-e-toolbar) { border-bottom: 1px solid var(--el-border-color-lighter) !important; }
.sidebar-card {
  background: var(--el-bg-color); border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px; padding: 16px; margin-bottom: 12px;
}
.card-title {
  font-size: 13px; font-weight: 600; margin-bottom: 8px;
  display: flex; justify-content: space-between; align-items: center;
}
.slug-row { display: flex; gap: 6px; }
</style>
