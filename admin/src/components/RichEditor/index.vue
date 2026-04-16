<template>
  <div class="rich-editor">
    <div ref="toolbarEl" class="editor-toolbar"></div>
    <div ref="editorEl" class="editor-content"></div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, watch } from 'vue'
import fileApi from '@/api/modules/file'

const props = defineProps<{
  modelValue?: string
  placeholder?: string
  disabled?: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

const toolbarEl = ref<HTMLElement>()
const editorEl = ref<HTMLElement>()
let editor: any = null

onMounted(async () => {
  const wangEditor = await import('@wangeditor/editor')
  await import('@wangeditor/editor/dist/css/style.css')

  editor = wangEditor.createEditor({
    selector: editorEl.value!,
    html: props.modelValue || '',
    config: {
      placeholder: props.placeholder || '写点什么...',
      onChange(ed: any) {
        const html = ed.getHtml()
        emit('update:modelValue', html === '<p><br></p>' ? '' : html)
      },
      MENU_CONF: {
        uploadImage: {
          async customUpload(file: File, insertFn: (url: string, alt: string, href: string) => void) {
            try {
              const result = await fileApi.uploadImage(file, { category: 'article' })
              if (result?.url) {
                insertFn(result.url, file.name, result.url)
              }
            } catch (e: any) {
              console.error('upload failed', e)
            }
          }
        }
      }
    },
  })

  wangEditor.createToolbar({
    editor,
    selector: toolbarEl.value!,
    config: {},
  })
})

watch(() => props.modelValue, (val) => {
  if (editor && val !== editor.getHtml()) {
    editor.setHtml(val || '')
  }
})

onBeforeUnmount(() => {
  editor?.destroy()
})
</script>

<style scoped>
.rich-editor {
  border: 1px solid var(--el-border-color, #e1e3e4);
  border-radius: 8px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
.editor-toolbar {
  border-bottom: 1px solid var(--el-border-color-lighter, #f0f0f0);
  flex-shrink: 0;
}
.editor-content {
  min-height: 350px;
  max-height: 60vh;
  overflow-y: auto;
}
</style>
