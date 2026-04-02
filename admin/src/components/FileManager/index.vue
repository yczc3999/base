<template>
  <el-dialog v-model="visible" title="文件管理器" width="900px" destroy-on-close top="5vh">
    <!-- 工具栏 -->
    <div class="fm-toolbar">
      <el-button type="primary" size="small" @click="triggerUpload">⬆ 上传文件</el-button>
      <input ref="fileInputRef" type="file" :accept="accept" :multiple="multiple" style="display:none" @change="handleFileSelect" />
      <div class="fm-toolbar-right">
        <el-input v-model="keyword" placeholder="搜索文件名..." size="small" clearable style="width:200px" @input="handleSearch" />
      </div>
    </div>

    <!-- 类型筛选 -->
    <div class="fm-filters">
      <button v-for="f in filters" :key="f.value" class="fm-filter" :class="{ active: activeFilter === f.value }" @click="activeFilter = f.value; loadFiles()">
        {{ f.label }}
      </button>
    </div>

    <!-- 文件网格 -->
    <div class="fm-grid" v-loading="loading">
      <div v-for="f in files" :key="f.id" class="fm-item" :class="{ selected: selectedIds.has(f.id) }" @click="toggleSelect(f)">
        <div class="fm-thumb">
          <img v-if="isImage(f.mime_type)" :src="f.url" :alt="f.original_name" />
          <span v-else class="fm-file-icon">{{ getFileIcon(f.ext) }}</span>
        </div>
        <div class="fm-name" :title="f.original_name">{{ f.original_name }}</div>
        <div v-if="selectedIds.has(f.id)" class="fm-check">✓</div>
      </div>
      <div v-if="!loading && !files.length" class="fm-empty">暂无文件</div>
    </div>

    <!-- 底部 -->
    <template #footer>
      <div class="fm-footer">
        <span class="fm-count">已选择 <strong>{{ selectedIds.size }}</strong> / {{ limit }}</span>
        <div>
          <el-button @click="visible = false">取消</el-button>
          <el-button type="primary" :disabled="!selectedIds.size" @click="handleConfirm">确 定</el-button>
        </div>
      </div>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, reactive, watch } from 'vue'
import fileApi from '@/api/modules/file'
import { ElMessage } from 'element-plus'

const props = withDefaults(defineProps<{
  modelValue: boolean
  accept?: string
  multiple?: boolean
  limit?: number
  category?: string
}>(), {
  accept: '*',
  multiple: false,
  limit: 1,
  category: '',
})

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  select: [files: any[]]
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const filters = [
  { label: '全部', value: '' },
  { label: '图片', value: 'image' },
  { label: '文档', value: 'document' },
  { label: '视频', value: 'video' },
  { label: '其他', value: 'other' },
]

const loading = ref(false)
const files = ref<any[]>([])
const selectedIds = reactive(new Set<number>())
const selectedMap = reactive(new Map<number, any>())
const keyword = ref('')
const activeFilter = ref('')
const fileInputRef = ref<HTMLInputElement>()

// 加载文件列表
async function loadFiles() {
  loading.value = true
  try {
    const params: any = { page: 1, pageSize: 60 }
    if (keyword.value) params.keyword = keyword.value
    if (props.category) params.filters = JSON.stringify({ category: props.category })
    if (activeFilter.value) {
      const mimeFilter = activeFilter.value === 'image' ? { mime_type: { op: 'prefix', value: 'image/' } }
        : activeFilter.value === 'video' ? { mime_type: { op: 'prefix', value: 'video/' } }
        : activeFilter.value === 'document' ? { mime_type: { op: 'in', value: ['application/pdf', 'application/msword', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'application/vnd.ms-excel', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'] } }
        : {}
      params.filters = JSON.stringify({ ...JSON.parse(params.filters || '{}'), ...mimeFilter })
    }
    const data = await fileApi.getList(params)
    files.value = data?.list || []
  } catch {}
  loading.value = false
}

function handleSearch() {
  loadFiles()
}

// 选择
function toggleSelect(f: any) {
  if (selectedIds.has(f.id)) {
    selectedIds.delete(f.id)
    selectedMap.delete(f.id)
  } else if (selectedIds.size < props.limit) {
    selectedIds.add(f.id)
    selectedMap.set(f.id, f)
  }
}

// 确定
function handleConfirm() {
  const selected = Array.from(selectedMap.values())
  emit('select', selected)
  visible.value = false
}

// 上传
function triggerUpload() {
  fileInputRef.value?.click()
}

async function handleFileSelect(e: Event) {
  const input = e.target as HTMLInputElement
  const fileList = input.files
  if (!fileList?.length) return

  for (const f of Array.from(fileList)) {
    try {
      const result = await fileApi.upload(f, { category: props.category || 'default' })
      ElMessage.success(`${f.name} 上传成功`)
    } catch (err: any) {
      ElMessage.error(`${f.name} 上传失败: ${err.message}`)
    }
  }

  input.value = ''
  loadFiles()
}

// 工具
function isImage(mime: string) { return mime?.startsWith('image/') }
function getFileIcon(ext: string) {
  const map: Record<string, string> = { pdf: '📄', doc: '📝', docx: '📝', xls: '📊', xlsx: '📊', zip: '📦', rar: '📦', mp4: '🎬', mp3: '🎵' }
  return map[ext] || '📎'
}

// 打开时加载
watch(visible, (v) => {
  if (v) { selectedIds.clear(); selectedMap.clear(); loadFiles() }
})
</script>

<style scoped lang="scss">
.fm-toolbar {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 12px;
}
.fm-toolbar-right { display: flex; gap: 8px; }
.fm-filters {
  display: flex; gap: 4px; margin-bottom: 12px;
}
.fm-filter {
  padding: 4px 12px; font-size: 12px; font-weight: 500; font-family: inherit;
  background: var(--bg-page, #F1F5F9); border: 1px solid var(--border, #E2E8F0);
  border-radius: 4px; cursor: pointer; color: var(--text-secondary, #64748B);
  transition: all 0.1s;
  &:hover { color: var(--primary, #2563EB); border-color: var(--primary, #2563EB); }
  &.active { background: var(--primary, #2563EB); color: white; border-color: var(--primary, #2563EB); }
}
.fm-grid {
  display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px;
  max-height: 400px; overflow-y: auto; min-height: 200px;
}
.fm-item {
  border: 2px solid var(--border, #E2E8F0); border-radius: 4px; cursor: pointer;
  position: relative; overflow: hidden; transition: border-color 0.15s;
  &:hover { border-color: #CBD5E1; }
  &.selected { border-color: var(--primary, #2563EB); }
}
.fm-thumb {
  aspect-ratio: 1; background: #F8FAFC; display: flex; align-items: center; justify-content: center; overflow: hidden;
  img { width: 100%; height: 100%; object-fit: cover; }
}
.fm-file-icon { font-size: 32px; }
.fm-name {
  padding: 4px 6px; font-size: 11px; color: var(--text-secondary, #64748B);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  border-top: 1px solid var(--border, #E2E8F0); background: #FAFBFC;
}
.fm-check {
  position: absolute; top: 4px; right: 4px; width: 20px; height: 20px;
  background: var(--primary, #2563EB); color: white; border-radius: 4px;
  display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 700;
}
.fm-empty {
  grid-column: 1 / -1; text-align: center; padding: 40px; color: #94A3B8; font-size: 13px;
}
.fm-footer {
  display: flex; align-items: center; justify-content: space-between; width: 100%;
  .fm-count { font-size: 13px; color: var(--text-secondary, #64748B); strong { color: var(--primary, #2563EB); } }
}
</style>
