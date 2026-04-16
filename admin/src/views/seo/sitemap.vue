<template>
  <div v-loading="loading" class="seo-sitemap">
    <section class="header-bar">
      <div>
        <h2>sitemap 管理</h2>
        <span class="subtitle">分片文件列表 + 强制重建 + 策略参考</span>
      </div>
      <el-button type="primary" :icon="Refresh" :loading="rebuilding" @click="rebuild">
        强制重建
      </el-button>
    </section>

    <!-- 文件列表卡 -->
    <section class="card">
      <div class="card-title">📁 sitemap 文件</div>
      <div v-if="files.length === 0" class="empty">
        <div class="empty-icon">🗺️</div>
        <p class="empty-title">还没有 sitemap 文件</p>
        <p class="empty-subtitle">点击右上角「强制重建」生成</p>
      </div>
      <el-table v-else :data="files" class="file-table" size="default">
        <el-table-column prop="name" label="文件" min-width="220">
          <template #default="{ row }">
            <span class="file-name">
              <span class="file-badge" :class="row.name === 'sitemap.xml' ? 'badge-index' : 'badge-chunk'">
                {{ row.name === 'sitemap.xml' ? 'INDEX' : 'CHUNK' }}
              </span>
              {{ row.name }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="大小" width="140">
          <template #default="{ row }">
            <span class="mono">{{ formatSize(row.size_bytes) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="最后修改" width="200">
          <template #default="{ row }">
            <span class="mono muted">{{ formatTime(row.mtime) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="120">
          <template #default="{ row }">
            <el-button size="small" link @click="view(row.name)">查看</el-button>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <!-- 上次重建 stats -->
    <section v-if="lastBuild" class="card stats-card">
      <div class="card-title">⚡ 上次重建</div>
      <div class="stats-row">
        <div class="stat">
          <span class="stat-label">URL 总数</span>
          <span class="stat-value">{{ lastBuild.total_urls?.toLocaleString() || 0 }}</span>
        </div>
        <div class="stat">
          <span class="stat-label">耗时</span>
          <span class="stat-value">{{ lastBuild.elapsed_ms }}<span class="unit">ms</span></span>
        </div>
        <div class="stat">
          <span class="stat-label">文件数</span>
          <span class="stat-value">{{ lastBuild.files?.length || 0 }}</span>
        </div>
      </div>
      <div v-if="lastBuild.base_url" class="base-url">
        <span class="label">base URL</span>
        <code>{{ lastBuild.base_url }}</code>
      </div>
    </section>

    <!-- 策略说明 -->
    <section class="card info-card">
      <div class="card-title">💡 策略说明</div>
      <ul class="info-list">
        <li><b>单文件最大 5,000 URL</b>（Google 硬限 50k，留余量便于增量更新）</li>
        <li><b>只写 &lt;lastmod&gt;</b>，不写 priority/changefreq（Google 已忽略）</li>
        <li><b>索引文件 + 分片结构</b>，符合 Google 2026 规范</li>
        <li><b>事件驱动 + 每小时定时</b>，发布文章时自动增量重建</li>
      </ul>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { get, post } from '@/api/request'

const files = ref<any[]>([])
const loading = ref(false)
const rebuilding = ref(false)
const lastBuild = ref<any>(null)

async function load() {
  loading.value = true
  try {
    const res = await get('/admin/seo/sitemap/files')
    files.value = res?.files || []
  } finally { loading.value = false }
}

async function rebuild() {
  rebuilding.value = true
  try {
    const res = await post('/admin/seo/sitemap/rebuild')
    lastBuild.value = res
    ElMessage.success(`已重建 ${res?.files?.length || 0} 文件，共 ${res?.total_urls} URL`)
    await load()
  } catch (e: any) {
    ElMessage.error(e?.message || '重建失败')
  } finally { rebuilding.value = false }
}

async function view(name: string) {
  const baseUrl = (lastBuild.value?.base_url) || ''
  if (baseUrl) {
    window.open(`${baseUrl}/${name}`, '_blank')
  } else {
    ElMessage.info(`文件路径：storage/seo/${name}（前端 URL 未配置无法预览）`)
  }
}

function formatSize(b: number): string {
  if (b < 1024) return `${b} B`
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`
  return `${(b / 1024 / 1024).toFixed(1)} MB`
}
function formatTime(t: number): string {
  return new Date(t * 1000).toLocaleString('zh-CN', { hour12: false })
}

onMounted(load)
</script>

<style scoped>
.seo-sitemap {
  padding: 32px;
  max-width: 900px;
  margin: 0 auto;
  display: flex; flex-direction: column; gap: 20px;
  font-family: 'Inter', 'PingFang SC', 'Microsoft YaHei', sans-serif;
}
.header-bar {
  display: flex; justify-content: space-between; align-items: center;
}
.header-bar h2 { font-size: 22px; font-weight: 800; color: #0f172a; margin: 0; letter-spacing: -0.02em; }
.subtitle { font-size: 13px; color: #64748b; }

.card {
  background: #fff; border: 1px solid #e2e8f0; border-radius: 8px; padding: 24px;
}
.card-title {
  font-size: 14px; font-weight: 700; color: #0f172a; margin-bottom: 16px;
}
.mono { font-family: ui-monospace, 'SF Mono', Monaco, monospace; font-size: 12px; }
.muted { color: #64748b; }

.empty {
  padding: 48px; text-align: center;
}
.empty-icon { font-size: 36px; margin-bottom: 12px; }
.empty-title { font-size: 15px; font-weight: 700; color: #0f172a; margin: 0; }
.empty-subtitle { font-size: 13px; color: #64748b; margin: 4px 0 0; }

.file-table :deep(.el-table__row:hover td) { background: #f8fafc !important; }
.file-name { display: flex; align-items: center; gap: 10px; font-weight: 500; }
.file-badge {
  padding: 2px 8px; border-radius: 3px; font-size: 10px; font-weight: 800;
  letter-spacing: 0.1em;
}
.badge-index { background: #dbeafe; color: #1e40af; }
.badge-chunk { background: #f1f5f9; color: #475569; }

.stats-card .stats-row { display: flex; gap: 40px; margin-bottom: 16px; }
.stat { display: flex; flex-direction: column; }
.stat-label {
  font-size: 10px; font-weight: 700; color: #94a3b8;
  text-transform: uppercase; letter-spacing: 0.15em; margin-bottom: 6px;
}
.stat-value { font-size: 26px; font-weight: 900; color: #0f172a; line-height: 1; }
.unit { font-size: 13px; font-weight: 600; color: #94a3b8; margin-left: 4px; }
.base-url {
  padding: 10px 14px; background: #f8fafc; border-radius: 4px;
  font-size: 12px; display: flex; gap: 10px; align-items: center;
}
.base-url .label {
  font-size: 10px; font-weight: 700; color: #94a3b8;
  text-transform: uppercase; letter-spacing: 0.15em;
}
.base-url code {
  font-family: ui-monospace, monospace; color: #0f172a; font-size: 12px;
}

.info-card { background: #eff6ff; border-color: #dbeafe; }
.info-list { margin: 0; padding-left: 20px; font-size: 13px; color: #334155; line-height: 2; }
.info-list b { color: #0f172a; }
</style>
