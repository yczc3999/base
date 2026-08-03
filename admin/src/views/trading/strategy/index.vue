<template>
  <div class="strategy-page">
    <el-card v-loading="loading">
      <template #header>
        <div class="card-header">
          <span>策略控制 — {{ form.name || '未配置' }}</span>
          <el-tag v-if="form.mode" :type="modeTagType" effect="dark">{{ form.mode }}</el-tag>
        </div>
      </template>

      <el-alert type="warning" :closable="false" class="mode-alert">
        保存后立即原子写入 bot 的 control.json（bot 每 10s 重读）。
        LIVE 模式会用真实资金下单，切换前请确认。
      </el-alert>

      <el-form label-width="120px" style="max-width: 720px">
        <el-form-item label="运行模式">
          <el-radio-group v-model="form.mode">
            <el-radio-button value="PAPER">PAPER 纸面</el-radio-button>
            <el-radio-button value="SHADOW">SHADOW 影子</el-radio-button>
            <el-radio-button value="LIVE">LIVE 实盘</el-radio-button>
          </el-radio-group>
        </el-form-item>

        <el-form-item label="单笔上限($)">
          <el-input-number v-model="params.max_position_usd" :min="0" :precision="2" />
        </el-form-item>
        <el-form-item label="最大持仓数">
          <el-input-number v-model="params.max_open_positions" :min="0" :precision="0" />
        </el-form-item>
        <el-form-item label="日亏损熔断($)">
          <el-input-number v-model="params.daily_loss_breaker_usd" :min="0" :precision="2" />
        </el-form-item>
        <el-form-item label="滑点(bps)">
          <el-input-number v-model="params.slippage_bps" :min="0" :precision="0" />
        </el-form-item>
        <el-form-item label="Gas上限(gwei)">
          <el-input-number v-model="params.gas_price_cap_gwei" :min="0" :precision="1" />
        </el-form-item>

        <el-form-item label="全部参数">
          <JsonEditor v-model="paramsJson" />
        </el-form-item>

        <el-form-item>
          <el-button type="primary" :loading="saving" @click="save">保存并下发</el-button>
          <el-button @click="load">重置</el-button>
          <span class="updated-at" v-if="updatedAt">上次更新：{{ updatedAt }}</span>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import JsonEditor from '@/components/JsonEditor/index.vue'
import { createCrudApi } from '@/api/crud'

const api = createCrudApi('admin/trading/strategy')

const loading = ref(false)
const saving = ref(false)
const strategyId = ref<number | null>(null)
const updatedAt = ref('')
const form = reactive({ name: '', mode: 'PAPER' })
const params = reactive<Record<string, any>>({
  max_position_usd: 400,
  max_open_positions: 3,
  daily_loss_breaker_usd: 200,
  slippage_bps: 50,
  gas_price_cap_gwei: 5,
})
// JsonEditor 的字符串视图（与上方数字字段双向同步）
const paramsJson = ref('')

let syncing = false
watch(params, () => {
  if (syncing) return
  syncing = true
  paramsJson.value = JSON.stringify(params, null, 2)
  syncing = false
}, { deep: true })
watch(paramsJson, (val) => {
  if (syncing) return
  try {
    const obj = val ? JSON.parse(val) : {}
    if (obj && typeof obj === 'object' && !Array.isArray(obj)) {
      syncing = true
      Object.keys(params).forEach(k => { delete params[k] })
      Object.assign(params, obj)
      syncing = false
    }
  } catch { /* 输入中途的非法 JSON 不同步 */ }
})

const modeTagType = computed(() =>
  form.mode === 'LIVE' ? 'danger' : form.mode === 'SHADOW' ? 'warning' : 'info'
)

async function load() {
  loading.value = true
  try {
    const data = await api.getList({ page: 1, pageSize: 1 })
    const row = data?.list?.[0]
    if (!row) {
      ElMessage.warning('strategies 表为空，请先执行迁移 seed')
      return
    }
    strategyId.value = row.id
    form.name = row.name
    form.mode = row.mode
    updatedAt.value = (row.updated_at || '').replace('T', ' ').slice(0, 19)
    const p = typeof row.params === 'string' ? JSON.parse(row.params) : (row.params || {})
    syncing = true
    Object.keys(params).forEach(k => { delete params[k] })
    Object.assign(params, p)
    paramsJson.value = JSON.stringify(p, null, 2)
    syncing = false
  } catch (e: any) {
    ElMessage.error(e?.message || '加载失败')
  } finally {
    loading.value = false
  }
}

async function save() {
  if (!strategyId.value) return
  if (form.mode === 'LIVE') {
    await ElMessageBox.confirm(
      '确认切换到 LIVE 实盘模式？保存后立即下发，bot 10s 内生效，将用真实资金下单。',
      '⚠️ 实盘确认', { confirmButtonText: '确认切换 LIVE', cancelButtonText: '取消', type: 'error' }
    )
  }
  saving.value = true
  try {
    const res = await api.doEdit({
      id: strategyId.value,
      mode: form.mode,
      params: JSON.stringify(params),
    })
    updatedAt.value = (res?.updated_at || '').replace('T', ' ').slice(0, 19)
    ElMessage.success('已保存并下发 control.json')
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.strategy-page { max-width: 860px; }
.card-header { display: flex; align-items: center; gap: 10px; font-weight: 600; }
.mode-alert { margin-bottom: 18px; }
.updated-at { margin-left: 12px; font-size: 12px; color: var(--el-text-color-secondary); }
</style>
