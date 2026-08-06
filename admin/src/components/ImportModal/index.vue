<template>
  <el-dialog v-model="visible" :title="`导入数据 — ${module}`" width="560px" destroy-on-close>
    <div class="import-body">
      <div class="import-step">
        <span class="step-num">1</span>
        <span>下载模板，按表头填写数据</span>
        <el-button link type="primary" :icon="Download" :loading="tplLoading" @click="downloadTemplate">
          下载模板
        </el-button>
      </div>
      <div class="import-step">
        <span class="step-num">2</span>
        <span>上传填写好的文件</span>
        <el-upload
          :show-file-list="false"
          accept=".xlsx,.xls"
          :before-upload="onUpload"
        >
          <el-button type="primary" :icon="Upload" :loading="uploading">
            {{ uploading ? '导入中...' : '选择文件导入' }}
          </el-button>
        </el-upload>
      </div>

      <!-- 结果 -->
      <div v-if="result" class="import-result">
        <div class="result-summary">
          <span class="ok">✅ 成功 {{ result.imported }}</span>
          <span class="bad" :class="{ warn: result.failed > 0 }">❌ 失败 {{ result.failed }}</span>
        </div>
        <div v-if="result.errors?.length" class="error-list">
          <div v-for="(e, i) in result.errors.slice(0, 50)" :key="i" class="error-line">
            第 {{ e.row }} 行：{{ e.error }}
          </div>
          <div v-if="result.errors.length > 50" class="error-more">
            其余 {{ result.errors.length - 50 }} 条略...
          </div>
        </div>
      </div>
    </div>

    <template #footer>
      <el-button @click="visible = false">关闭</el-button>
      <el-button v-if="result?.failed === 0 && result?.imported > 0" type="primary" @click="done">
        完成
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Download, Upload } from '@element-plus/icons-vue'
import { getToken } from '@/utils/auth'

const props = defineProps<{ module: string }>()

const visible = defineModel<boolean>({ default: false })
const emit = defineEmits<{ (e: 'success'): void }>()

const tplLoading = ref(false)
const uploading = ref(false)
const result = ref<any>(null)

async function downloadTemplate() {
  tplLoading.value = true
  try {
    const { default: axios } = await import('axios')
    const BASE = import.meta.env.VITE_API_BASE_URL || ''
    const PREFIX = import.meta.env.VITE_API_PREFIX || '/api'
    const resp = await axios.get(`${BASE}${PREFIX}/admin/import/template`, {
      params: { module: props.module },
      headers: { Authorization: `Bearer ${getToken()}` },
      responseType: 'blob',
    })
    const url = URL.createObjectURL(resp.data)
    const a = document.createElement('a')
    a.href = url
    a.download = `import_${props.module}_template.xlsx`
    a.click()
    URL.revokeObjectURL(url)
  } catch {
    ElMessage.error('模板下载失败')
  } finally { tplLoading.value = false }
}

async function onUpload(file: File) {
  uploading.value = true
  result.value = null
  const fd = new FormData()
  fd.append('file', file)
  fd.append('module', props.module)
  try {
    const { default: axios } = await import('axios')
    const BASE = import.meta.env.VITE_API_BASE_URL || ''
    const PREFIX = import.meta.env.VITE_API_PREFIX || '/api'
    const resp = await axios.post(`${BASE}${PREFIX}/admin/import/upload`, fd, {
      headers: {
        Authorization: `Bearer ${getToken()}`,
        'Content-Type': 'multipart/form-data',
      },
    })
    const data = resp.data
    if (data.code !== 0) {
      ElMessage.error(data.msg || '导入失败')
      return false
    }
    result.value = data.data
    if (data.data?.failed === 0) ElMessage.success(`导入成功 ${data.data.imported} 条`)
    else ElMessage.warning(`导入完成：成功 ${data.data.imported}，失败 ${data.data.failed}`)
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.msg || '导入失败')
  } finally { uploading.value = false }
  return false  // 阻止 el-upload 默认上传
}

function done() {
  emit('success')
  visible.value = false
}
</script>

<style scoped>
.import-body { display: flex; flex-direction: column; gap: 16px; }
.import-step {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
}
.step-num {
  width: 20px; height: 20px;
  display: inline-flex; align-items: center; justify-content: center;
  background: var(--primary);
  color: #fff; border-radius: 50%;
  font-size: 12px; flex-shrink: 0;
}
.import-result {
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 12px;
}
.result-summary { display: flex; gap: 16px; font-weight: 600; margin-bottom: 8px; }
.result-summary .ok { color: var(--success); }
.result-summary .bad { color: var(--danger); }
.result-summary .bad.warn { color: var(--danger); }
.error-list {
  max-height: 160px; overflow-y: auto;
  font-size: 12px; line-height: 1.8;
  color: var(--danger);
}
.error-more { color: var(--text-secondary); }
</style>
