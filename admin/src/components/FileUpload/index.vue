<template>
  <div class="file-upload">
    <!-- 拖拽区域 -->
    <div
class="dropzone" :class="{ dragover }" @click="openManager"
      @dragover.prevent="dragover = true" @dragleave="dragover = false"
      @drop.prevent="handleDrop">
      <span class="dropzone-icon">{{ isPrivate ? '🔒' : '☁' }}</span>
      <span class="dropzone-text">拖拽文件到此处或 <em>点击选择</em></span>
    </div>

    <!-- 文件列表 -->
    <div v-if="fileList.length" class="file-list">
      <div v-for="(f, idx) in fileList" :key="idx" class="file-item">
        <span class="file-icon" :class="getIconClass(f.ext || f.name)">{{ getIconText(f.ext || f.name) }}</span>
        <span class="file-name">{{ f.original_name || f.name }}</span>
        <span class="file-size">{{ formatSize(f.size) }}</span>
        <button class="file-del" @click="removeFile(idx)">✕</button>
      </div>
    </div>

    <div v-if="hint" class="file-hint">
      {{ hint }}
      <span v-if="isPrivate" class="private-tag">🔒 隐私文件 · 本地存储</span>
    </div>

    <!-- 文件管理器 -->
    <FileManager
      v-model="managerVisible"
      :accept="accept"
      :multiple="multiple"
      :limit="limit - fileList.length"
      :category="category"
      @select="handleSelect"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import FileManager from '@/components/FileManager/index.vue'
import fileApi from '@/api/modules/file'
import { ElMessage } from 'element-plus/es/components/message/index'

const props = withDefaults(defineProps<{
  modelValue: string | string[]
  accept?: string
  multiple?: boolean
  limit?: number
  category?: string
  isPrivate?: boolean
  hint?: string
}>(), {
  accept: '*',
  multiple: true,
  limit: 5,
  category: 'document',
  isPrivate: false,
  hint: '',
})

const emit = defineEmits<{
  'update:modelValue': [value: string | string[]]
}>()

const managerVisible = ref(false)
const dragover = ref(false)

// 解析 v-model 为文件信息列表
const fileList = computed<any[]>(() => {
  if (!props.modelValue) return []
  const arr = Array.isArray(props.modelValue) ? props.modelValue : [props.modelValue]
  return arr.filter(Boolean).map((url) => {
    if (typeof url === 'object') return url
    const parts = url.split('/')
    const name = parts[parts.length - 1] || url
    const ext = name.includes('.') ? name.split('.').pop()! : ''
    return { url, name, original_name: name, ext, size: 0 }
  })
})

function openManager() { managerVisible.value = true }

function handleSelect(files: any[]) {
  const newUrls = files.map((f: any) => f.url)
  if (props.multiple) {
    const current = Array.isArray(props.modelValue) ? [...props.modelValue] : props.modelValue ? [props.modelValue] : []
    const merged = [...current, ...newUrls].slice(0, props.limit)
    emit('update:modelValue', merged)
  } else {
    emit('update:modelValue', newUrls[0] || '')
  }
}

function removeFile(idx: number) {
  if (props.multiple) {
    const arr = Array.isArray(props.modelValue) ? [...props.modelValue] : [props.modelValue]
    arr.splice(idx, 1)
    emit('update:modelValue', arr)
  } else {
    emit('update:modelValue', '')
  }
}

async function handleDrop(e: DragEvent) {
  dragover.value = false
  const files = e.dataTransfer?.files
  if (!files?.length) return
  for (const f of Array.from(files)) {
    try {
      const result = await fileApi.upload(f, { category: props.category, is_private: props.isPrivate })
      handleSelect([result])
      ElMessage.success(`${f.name} 上传成功`)
    } catch (err: any) {
      ElMessage.error(`${f.name}: ${err.message}`)
    }
  }
}

function formatSize(bytes: number) {
  if (!bytes) return ''
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)}KB`
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`
}

function getIconClass(name: string) {
  const ext = name.includes('.') ? name.split('.').pop()?.toLowerCase() : name.toLowerCase()
  if (['pdf'].includes(ext || '')) return 'pdf'
  if (['xls', 'xlsx'].includes(ext || '')) return 'xlsx'
  if (['doc', 'docx'].includes(ext || '')) return 'doc'
  if (['zip', 'rar', '7z'].includes(ext || '')) return 'zip'
  return 'other'
}

function getIconText(name: string) {
  const ext = name.includes('.') ? name.split('.').pop()?.toUpperCase() : name.toUpperCase()
  return ext?.slice(0, 4) || 'FILE'
}
</script>

<style scoped lang="scss">
.dropzone {
  border: 2px dashed #CBD5E1; border-radius: 4px; padding: 24px;
  text-align: center; cursor: pointer; background: #F8FAFC; transition: all 0.15s;
  &:hover, &.dragover { border-color: var(--primary, #2563EB); background: #EFF6FF; }
  .dropzone-icon { font-size: 28px; display: block; margin-bottom: 4px; }
  .dropzone-text { font-size: 13px; color: #64748B; em { color: var(--primary, #2563EB); font-style: normal; font-weight: 500; } }
}

.file-list { margin-top: 8px; display: flex; flex-direction: column; gap: 4px; }

.file-item {
  display: flex; align-items: center; gap: 8px; padding: 8px 10px;
  background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 4px; font-size: 13px;
}

.file-icon {
  width: 28px; height: 28px; border-radius: 4px; display: flex; align-items: center; justify-content: center;
  font-size: 9px; font-weight: 700; flex-shrink: 0; font-family: 'Geist Mono', monospace;
  &.pdf { background: #FEE2E2; color: #EF4444; }
  &.xlsx { background: #DCFCE7; color: #16A34A; }
  &.doc { background: #DBEAFE; color: #2563EB; }
  &.zip { background: #F1F5F9; color: #64748B; }
  &.other { background: #F3F4F6; color: #6B7280; }
}

.file-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 500; }
.file-size { font-size: 12px; color: #94A3B8; flex-shrink: 0; }

.file-del {
  width: 22px; height: 22px; display: flex; align-items: center; justify-content: center;
  background: none; border: 1px solid transparent; border-radius: 4px;
  color: #94A3B8; cursor: pointer; font-size: 14px;
  &:hover { background: #FEE2E2; color: #EF4444; border-color: #EF4444; }
}

.file-hint {
  font-size: 12px; color: #94A3B8; margin-top: 8px;
  .private-tag { color: #B45309; font-weight: 500; }
}
</style>
