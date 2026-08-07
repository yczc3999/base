<template>
  <div>
    <div class="cache-header">
      <el-button type="primary" :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
      <span class="tip">Redis 总 key 数：<b>{{ stats?.dbsize ?? '—' }}</b></span>
    </div>

    <!-- 加载/空态 -->
    <EmptyState
      v-if="!stats"
      :icon="DatabaseIcon"
      :title="loading ? '加载中...' : '暂无缓存数据'"
      description="点击刷新重新获取"
    />
    <EmptyState
      v-else-if="!stats.modules?.length"
      :icon="DatabaseIcon"
      title="未发现缓存模块"
    />

    <div v-else class="cache-grid">
      <div v-for="m in stats.modules" :key="m.prefix" class="cache-card">
        <div class="cache-info">
          <div class="cache-label">{{ m.label }}</div>
          <div class="cache-prefix"><code>{{ m.prefix }}</code></div>
        </div>
        <div class="cache-keys">
          <span class="keys-num">{{ m.keys }}</span>
          <span class="keys-label">个 key</span>
        </div>
        <el-button
type="warning" plain size="small" :icon="Delete"
          :loading="clearing === m.prefix" @click="clearModule(m)">
          清空
        </el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
defineOptions({ name: 'Cache' })
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index'
import { confirmDialog } from '@/utils/confirm'
import { Refresh, Delete } from '@element-plus/icons-vue'
import { Database as DatabaseIcon } from 'lucide-vue-next'
import { get, post } from '@/api/request'
import EmptyState from '@/components/EmptyState/index.vue'

const stats = ref<any>(null)
const clearing = ref('')
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    stats.value = await get('/admin/cache/stats')
  } catch { /* 统计加载失败静默 */ } finally { loading.value = false }
}

async function clearModule(m: any) {
  await confirmDialog(
    `确认清空「${m.label}」的 ${m.keys} 个缓存 key？清空后相关数据会重新从数据库读取。`,
    '清空缓存', { type: 'warning' },
  )
  clearing.value = m.prefix
  try {
    await post('/admin/cache/clear', { prefix: m.prefix })
    ElMessage.success('已清空')
    load()
  } catch { /* 清空失败静默处理 */ } finally { clearing.value = '' }
}

onMounted(load)
</script>

<style scoped>
.cache-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}
.tip { font-size: 13px; }
.cache-empty {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 40px 0;
}
.cache-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 12px;
}
.cache-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 16px;
}
.cache-label { font-size: 14px; font-weight: 600; }
.cache-prefix code {
  font-size: 12px;
  background: var(--border-light);
  padding: 1px 6px; border-radius: var(--radius-lg);
}
.cache-keys { display: flex; align-items: baseline; gap: 6px; }
.keys-num { font-size: 24px; font-weight: 700; color: var(--primary); }
.keys-label { font-size: 12px; color: var(--text-secondary); }
</style>
