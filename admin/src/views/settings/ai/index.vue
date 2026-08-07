<template>
  <div v-loading="loading" style="max-width:820px">
    <h2 class="settings-title">AI 配置</h2>

    <!-- ===== 1. Provider ===== -->
    <div class="card">
      <div class="card-title">模型接入</div>
      <el-form label-width="100px">
        <el-form-item label="AI 平台">
          <el-select v-model="form.provider" style="width:100%" @change="onProviderChange">
            <el-option
v-for="key in Object.keys(providerDefaults)" :key="key"
              :label="PROVIDER_LABELS[key] || key" :value="key" />
            <el-option label="自定义" value="custom" />
          </el-select>
        </el-form-item>
        <el-form-item label="API Key">
          <el-input v-model="form.api_key" type="password" show-password placeholder="sk-..." />
        </el-form-item>
        <el-form-item label="API 地址">
          <el-input v-model="form.base_url" placeholder="https://api.deepseek.com/v1" />
          <div class="form-hint">兼容 OpenAI 格式的 chat/completions 端点，切换平台时自动填充</div>
        </el-form-item>
        <el-form-item label="模型">
          <el-input v-model="form.model" placeholder="deepseek-chat" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :icon="Check" @click="save">保存</el-button>
          <el-button :loading="testing" @click="testConnection">
            {{ testing ? '测试中...' : '测试连接' }}
          </el-button>
        </el-form-item>
      </el-form>
    </div>

  </div>
</template>

<script setup lang="ts">
defineOptions({ name: 'SettingsAi' })
import { ref, reactive, onMounted } from 'vue'
import { createSettingApi } from '@/api/settings'
import { ElMessage } from 'element-plus/es/components/message/index'
import { Check } from '@element-plus/icons-vue'
import { get, post } from '@/api/request'

type ProviderDefault = { base_url: string; model: string }
const providerDefaults = ref<Record<string, ProviderDefault>>({})
const PROVIDER_LABELS: Record<string, string> = {
  deepseek: 'DeepSeek', minimax: 'MiniMax', glm: '智谱 GLM', openai: 'OpenAI',
}

const api = createSettingApi('ai')
const loading = ref(false)
const testing = ref(false)
const form = reactive({ provider: 'deepseek', api_key: '', base_url: '', model: '' })

onMounted(async () => {
  loading.value = true
  try {
    const [data, defaultsRes] = await Promise.all([
      api.getAll(),
      get('/admin/setting/ai/provider-defaults').catch(() => ({ defaults: {} })),
    ])
    providerDefaults.value = defaultsRes?.defaults || {}
    Object.assign(form, data)
  } finally { loading.value = false }
})

function onProviderChange(val: string) {
  if (val === 'custom') {
    form.base_url = ''
    form.model = ''
    return
  }
  const d = providerDefaults.value[val]
  if (d) {
    form.base_url = d.base_url
    form.model = d.model
  }
}

async function save() {
  await api.setMany(form)
  ElMessage.success('保存成功')
}

async function testConnection() {
  if (!form.api_key) return ElMessage.warning('请先填写 API Key')
  testing.value = true
  try {
    await save()
    const res = await post('/admin/setting/ai/test')
    ElMessage.success(res?.message || 'AI 连接正常')
  } catch {
    ElMessage.error('连接失败')
  } finally {
    testing.value = false
  }
}
</script>

<style scoped>
.card {
  background: var(--bg-card); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 24px; margin-bottom: 20px;
}
.card-title {
  font-size: 15px; font-weight: 600; margin-bottom: 16px;
  display: flex; align-items: baseline; gap: 10px;
}
.card-subtitle { font-size: 12px; font-weight: 400; color: var(--el-text-color-secondary); }
.form-hint { font-size: 12px; color: var(--el-text-color-secondary); margin-top: 4px; line-height: 1.6; }
.form-hint code { background: var(--el-fill-color); padding: 1px 6px; border-radius: 3px; font-size: 12px; }
</style>
