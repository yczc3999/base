<template>
  <div class="seo-log">
    <section class="header-bar">
      <div>
        <h2>发布日志</h2>
        <span class="subtitle">系统所有动作的时间线，支持按级别/动作/文章 ID 过滤</span>
      </div>
    </section>

    <CrudTable
ref="crudRef" api="admin/publish_log" perms="admin:seo"
      :columns="columns" :search-fields="searchFields"
      :has-create="false" :has-edit="false" :has-delete="false">
      <template #expand="{ row }">
        <div class="payload-detail">
          <div v-if="row.payload" class="payload-block">
            <div class="payload-label">请求内容</div>
            <pre>{{ formatPayload(row.payload) }}</pre>
          </div>
          <div v-else class="payload-empty">无 payload</div>
        </div>
      </template>
    </CrudTable>
  </div>
</template>

<script setup lang="ts">
defineOptions({ name: 'SeoLog' })
import CrudTable from '@/components/CrudTable/index.vue'
import type { CrudColumn, SearchField } from '@/components/CrudTable/types'

const columns: CrudColumn[] = [
  { field: 'id', label: 'ID', width: 80 },
  { field: 'created_at', label: '时间', width: 170, type: 'time', sortable: 'custom' },
  { field: 'level', label: '级别', width: 80, align: 'center', type: 'tag',
    tagMap: {
      info: { label: 'info', type: 'success' },
      warn: { label: 'warn', type: 'warning' },
      error: { label: 'error', type: 'danger' },
    } },
  { field: 'action', label: '动作', width: 130, type: 'tag',
    tagMap: {
      collect: { label: '采集', type: 'info' },
      rewrite: { label: '润色', type: 'info' },
      fingerprint: { label: '指纹', type: 'info' },
      schedule: { label: '排期', type: 'primary' },
      publish: { label: '发布', type: 'success' },
      skip: { label: '跳过', type: 'warning' },
      indexnow: { label: 'IndexNow', type: 'primary' },
      sitemap_rebuild: { label: 'sitemap', type: 'info' },
      phase_change: { label: '阶段切换', type: 'warning' },
      kill_switch: { label: '禁发', type: 'danger' },
      manual_approve: { label: '人工通过', type: 'success' },
      manual_cancel: { label: '人工取消', type: 'info' },
      error: { label: '错误', type: 'danger' },
    } },
  { field: 'article_id', label: '文章ID', width: 80 },
  { field: 'msg', label: '消息', minWidth: 300 },
]

const searchFields: SearchField[] = [
  { field: 'level', label: '级别', type: 'select', options: [
    { label: 'info', value: 'info' },
    { label: 'warn', value: 'warn' },
    { label: 'error', value: 'error' },
  ] },
  { field: 'action', label: '动作', type: 'input', placeholder: '如 publish / skip' },
  { field: 'article_id', label: '文章ID', type: 'input', placeholder: '文章 ID' },
]

function formatPayload(p: any): string {
  if (!p) return ''
  try {
    if (typeof p === 'string') return JSON.stringify(JSON.parse(p), null, 2)
    return JSON.stringify(p, null, 2)
  } catch {
    return String(p)
  }
}
</script>

<style scoped>
.seo-log {
  padding: 32px;
  max-width: 1400px;
  margin: 0 auto;
  font-family: 'Inter', 'PingFang SC', 'Microsoft YaHei', sans-serif;
}
.header-bar { margin-bottom: 24px; }
.header-bar h2 { font-size: 22px; font-weight: 800; color: #0f172a; margin: 0; letter-spacing: -0.02em; }
.subtitle { font-size: 13px; color: #64748b; }

.payload-detail { padding: 16px 40px; background: #f8fafc; border-left: 3px solid #3b82f6; }
.payload-label {
  font-size: 10px; color: #94a3b8; margin-bottom: 8px;
  text-transform: uppercase; letter-spacing: 0.15em; font-weight: 700;
}
.payload-block pre {
  margin: 0; font-size: 12px; line-height: 1.6;
  max-height: 320px; overflow: auto;
  background: #0f172a; color: #e2e8f0;
  padding: 14px 16px; border-radius: 6px;
  font-family: ui-monospace, 'SF Mono', Monaco, monospace;
}
.payload-empty { font-size: 12px; color: #94a3b8; font-style: italic; }
</style>
