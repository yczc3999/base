<template>
  <div v-loading="loading" class="setting-form">
    <!-- 全局配置 -->
    <div v-if="extras.length" class="setting-section">
      <div class="section-header">通用配置</div>
      <div class="section-body">
        <el-form label-width="140px">
          <el-form-item v-for="f in extras" :key="f.name" :label="f.label">
            <el-input v-if="!f.type || f.type === 'input'" v-model="extraData[f.name]" :placeholder="f.tip" />
            <el-input-number v-else-if="f.type === 'number'" v-model="extraData[f.name]" :min="0" />
            <el-switch v-else-if="f.type === 'switch'" v-model="extraData[f.name]" active-value="1" inactive-value="0" />
            <el-select v-else-if="f.type === 'select'" v-model="extraData[f.name]" style="width:100%">
              <el-option v-for="o in f.options" :key="o.value" :label="o.label" :value="o.value" />
            </el-select>
            <div v-if="f.tip && f.type !== 'input'" class="field-tip">{{ f.tip }}</div>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :icon="Check" @click="saveExtras">保存</el-button>
          </el-form-item>
        </el-form>
      </div>
    </div>

    <!-- 默认选择（单选模式） -->
    <div v-if="mode === 'single' && providers.length > 1" class="setting-section">
      <div class="section-header">
        <span>默认服务商</span>
        <el-select v-model="defaultProvider" style="width:200px;margin-left:12px" @change="saveDefault">
          <el-option v-for="p in providers" :key="p.key" :label="p.label" :value="p.key" />
        </el-select>
      </div>
    </div>

    <!-- Tab 切换服务商 -->
    <div class="setting-section">
      <el-tabs v-model="activeTab" type="border-card" class="provider-tabs">
        <el-tab-pane v-for="p in providers" :key="p.key" :name="p.key">
          <template #label>
            <span class="tab-label">
              <span v-if="p.icon" class="tab-icon">{{ p.icon }}</span>
              <span>{{ p.label }}</span>
              <el-tag v-if="mode === 'single' && defaultProvider === p.key" type="primary" size="small" class="tab-tag">默认</el-tag>
              <el-tag v-if="mode === 'parallel' && enabledMap[p.key] === '1'" type="success" size="small" class="tab-tag">已启用</el-tag>
            </span>
          </template>

          <div class="tab-content">
            <!-- 并行模式启用开关 -->
            <div v-if="mode === 'parallel'" class="enable-bar">
              <span>启用{{ p.label }}</span>
              <el-switch
                v-model="enabledMap[p.key]"
                active-value="1"
                inactive-value="0"
                @change="toggleProvider(p.key, $event as string)"
              />
            </div>

            <div v-if="p.desc" class="provider-desc">{{ p.desc }}</div>

            <el-form label-width="140px" style="max-width:600px">
              <el-form-item
                v-for="f in p.fields"
                :key="f.name"
                :label="f.label"
                :required="f.required"
              >
                <el-input
                  v-if="!f.type || f.type === 'input'"
                  v-model="providerData[p.key][f.name]"
                  :placeholder="f.placeholder || f.tip"
                />
                <el-input
                  v-else-if="f.type === 'password'"
                  v-model="providerData[p.key][f.name]"
                  type="password"
                  show-password
                  :placeholder="f.placeholder"
                />
                <el-input
                  v-else-if="f.type === 'textarea'"
                  v-model="providerData[p.key][f.name]"
                  type="textarea"
                  :rows="3"
                  :placeholder="f.placeholder"
                />
                <el-input-number
                  v-else-if="f.type === 'number'"
                  v-model="providerData[p.key][f.name]"
                  :min="0"
                />
                <el-switch
                  v-else-if="f.type === 'switch'"
                  v-model="providerData[p.key][f.name]"
                />
                <div v-if="f.tip" class="field-tip">{{ f.tip }}</div>
              </el-form-item>
              <el-form-item>
                <el-button type="primary" :icon="Check" @click="saveProvider(p.key)">保存</el-button>
                <el-button :icon="Connection" @click="$emit('test', p.key, providerData[p.key])">测试</el-button>
              </el-form-item>
            </el-form>
          </div>
        </el-tab-pane>
      </el-tabs>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { createSettingApi } from '@/api/settings'
import { Check, Connection } from "@element-plus/icons-vue"
import { ElMessage } from 'element-plus/es/components/message/index'
import type { SettingProvider, SettingExtra } from './types'

const props = withDefaults(defineProps<{
  category: string
  providers: SettingProvider[]
  mode?: 'single' | 'parallel'
  extras?: SettingExtra[]
}>(), {
  mode: 'single',
  extras: () => [],
})

defineEmits<{ test: [key: string, data: Record<string, any>] }>()

const api = createSettingApi(props.category)
const loading = ref(false)
const defaultProvider = ref('')
const activeTab = ref(props.providers[0]?.key || '')
const enabledMap = reactive<Record<string, string>>({})
const providerData = reactive<Record<string, Record<string, any>>>({})
const extraData = reactive<Record<string, any>>({})

for (const p of props.providers) {
  providerData[p.key] = {}
  for (const f of p.fields) {
    if (f.default !== undefined) providerData[p.key][f.name] = f.default
  }
}
for (const e of props.extras) {
  if (e.default !== undefined) extraData[e.name] = e.default
}

async function loadSettings() {
  loading.value = true
  try {
    const all = await api.getAll()
    defaultProvider.value = all.default || props.providers[0]?.key || ''

    for (const p of props.providers) {
      const raw = all[p.key]
      if (raw) {
        try { providerData[p.key] = typeof raw === 'string' ? JSON.parse(raw) : raw } catch { /* 单条解析失败不影响其他服务商 */ }
      }
      if (props.mode === 'parallel') {
        enabledMap[p.key] = all[`${p.key}_enabled`] || '0'
      }
    }
    for (const e of props.extras) {
      if (all[e.name] !== undefined) extraData[e.name] = all[e.name]
    }
  } catch { /* 读取配置失败静默处理 */ }
  loading.value = false
}

async function saveDefault() {
  await api.setDefault(defaultProvider.value)
  ElMessage.success('默认服务商已更新')
}

async function saveProvider(key: string) {
  await api.set(key, providerData[key])
  ElMessage.success('保存成功')
}

async function toggleProvider(key: string, value: string) {
  await api.set(`${key}_enabled`, value)
  ElMessage.success(value === '1' ? '已启用' : '已禁用')
}

async function saveExtras() {
  await api.setMany(extraData)
  ElMessage.success('保存成功')
}

onMounted(loadSettings)
</script>

<style scoped lang="scss">
.setting-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.setting-section {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
}

.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 20px;
  font-weight: 600;
  font-size: var(--text-base);
  border-bottom: 1px solid var(--border);
  background: #FAFBFC;
}

.section-body {
  padding: 20px;
  :deep(.el-form-item:last-child) { margin-bottom: 0; }
}

// Tab 样式覆写
.provider-tabs {
  border: none !important;
  box-shadow: none !important;
  background: transparent !important;

  :deep(.el-tabs__header) {
    background: #FAFBFC !important;
    border-bottom: 1px solid var(--border) !important;
    margin: 0 !important;
  }

  :deep(.el-tabs__content) {
    padding: 0 !important;
  }

  :deep(.el-tabs__item) {
    height: 44px;
    line-height: 44px;
    border-radius: 0 !important;
  }

  :deep(.el-tabs__item.is-active) {
    background: var(--bg-card) !important;
    font-weight: 600;
  }
}

.tab-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;

  .tab-icon { font-size: 16px; }
  .tab-tag { margin-left: 4px; transform: scale(0.85); }
}

.tab-content {
  padding: 24px;
}

.enable-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  margin-bottom: 20px;
  background: #FAFBFC;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  font-weight: 500;
  font-size: var(--text-sm);
}

.provider-desc {
  font-size: var(--text-xs);
  color: var(--text-secondary);
  margin-bottom: 20px;
}

.field-tip {
  font-size: var(--text-xs);
  color: var(--text-placeholder);
  margin-top: 4px;
}
</style>
