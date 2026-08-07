<template>
  <div class="image-upload">
    <div class="image-upload-list">
      <!-- 已上传的图片 -->
      <div v-for="(url, idx) in urlList" :key="idx" class="img-slot filled">
        <img :src="url" />
        <div class="img-overlay">
          <button class="btn-del" @click="removeImage(idx)">✕</button>
        </div>
      </div>

      <!-- 上传按钮（未达上限时显示） -->
      <div v-if="urlList.length < limit" class="img-slot empty" @click="openManager">
        <span class="plus">+</span>
        <span class="upload-text">上传</span>
      </div>
    </div>

    <div v-if="hint" class="image-hint">{{ hint }}</div>

    <!-- 文件管理器 -->
    <FileManager
      v-model="managerVisible"
      :accept="accept"
      :multiple="multiple"
      :limit="limit - urlList.length"
      :category="category"
      @select="handleSelect"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import FileManager from '@/components/FileManager/index.vue'

const props = withDefaults(defineProps<{
  /** v-model: 单图为 string，多图为 string[]（URL） */
  modelValue: string | string[]
  accept?: string
  multiple?: boolean
  limit?: number
  category?: string
  private?: boolean
  hint?: string
}>(), {
  accept: 'image/*',
  multiple: false,
  limit: 1,
  category: 'avatar',
  private: false,
  hint: '',
})

const emit = defineEmits<{
  'update:modelValue': [value: string | string[]]
}>()

const managerVisible = ref(false)

// 统一为数组处理
const urlList = computed<string[]>(() => {
  if (!props.modelValue) return []
  return Array.isArray(props.modelValue) ? props.modelValue.filter(Boolean) : [props.modelValue].filter(Boolean)
})

function openManager() {
  managerVisible.value = true
}

function handleSelect(files: any[]) {
  const newUrls = files.map((f: any) => f.url)

  if (props.multiple) {
    const merged = [...urlList.value, ...newUrls].slice(0, props.limit)
    emit('update:modelValue', merged)
  } else {
    emit('update:modelValue', newUrls[0] || '')
  }
}

function removeImage(idx: number) {
  if (props.multiple) {
    const arr = [...urlList.value]
    arr.splice(idx, 1)
    emit('update:modelValue', arr)
  } else {
    emit('update:modelValue', '')
  }
}
</script>

<style scoped lang="scss">
.image-upload-list {
  display: flex; gap: 8px; flex-wrap: wrap;
}

.img-slot {
  width: 104px; height: 104px; border-radius: 4px; position: relative; overflow: hidden;
  display: flex; flex-direction: column; align-items: center; justify-content: center; cursor: pointer;
}

.img-slot.empty {
  border: 2px dashed #CBD5E1; background: #F8FAFC; transition: all 0.15s;
  &:hover { border-color: var(--primary, #2563EB); background: #EFF6FF; }
  .plus { font-size: 24px; color: #94A3B8; line-height: 1; }
  .upload-text { font-size: 11px; color: #94A3B8; margin-top: 4px; }
  &:hover .plus, &:hover .upload-text { color: var(--primary, #2563EB); }
}

.img-slot.filled {
  border: 1px solid #E2E8F0;
  img { width: 100%; height: 100%; object-fit: cover; transition: opacity 0.2s; }
  &:hover img { opacity: 0.5; }
}

.img-overlay {
  position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
  opacity: 0; transition: opacity 0.2s;
  .filled:hover & { opacity: 1; }
}

.img-slot.filled:hover .img-overlay { opacity: 1; }

.btn-del {
  width: 28px; height: 28px; background: #EF4444; color: white; border: none;
  border-radius: 4px; font-size: 14px; cursor: pointer;
  &:hover { background: #DC2626; }
}

.image-hint { font-size: 12px; color: #94A3B8; margin-top: 8px; }
</style>
