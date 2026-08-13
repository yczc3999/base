<script setup lang="ts">
/** Gamma 同步 tag 目录：查看 + 本地处置。不能手建 tag。 */
import { ref, watch, computed } from 'vue'
import PageShell from '@/components/PageShell/index.vue'
import { PageState, StatusBadge } from '../_shared'
import { useTagsPage } from '@/queries/v2/tags'
import { saveTagDisposition, syncTagCatalog } from '@/api/v2/tags'
import type { TagDisposition, TagRow } from '@/api/v2/types'
import { ApiRequestError } from '@/api/request'

const filters = ref<Record<string, string>>({})
const cursor = ref<string | null>(null)
const asOf = ref<string | null>(null)
const limit = ref(50)
const syncing = ref(false)
const syncMsg = ref('')
const savingId = ref<string | null>(null)

const { data, isLoading, isError, displayError, denied, refetch } = useTagsPage({
  filters: filters, cursor: cursor, asOf: asOf, limit: limit,
})
watch(filters, () => { cursor.value = null; asOf.value = null }, { deep: true })

const rows = computed(() => data.value?.items ?? [])
const hasMore = computed(() => data.value?.has_more ?? false)
function nextPage() { cursor.value = data.value?.next_cursor ?? null; asOf.value = data.value?.as_of ?? null }

async function runSync() {
  syncing.value = true
  syncMsg.value = ''
  try {
    const result = await syncTagCatalog()
    syncMsg.value = `同步 ${result.upserted} 条 / ${result.pages} 页`
      + (result.truncated ? '（截断）' : '')
    cursor.value = null
    asOf.value = null
    await refetch()
  } catch (error) {
    syncMsg.value = error instanceof ApiRequestError ? error.message : '同步失败'
  } finally {
    syncing.value = false
  }
}

async function onDisposition(row: TagRow, value: TagDisposition | '') {
  savingId.value = row.id
  try {
    await saveTagDisposition(row.id, value === '' ? null : value)
    await refetch()
  } catch (error) {
    syncMsg.value = error instanceof ApiRequestError ? error.message : '处置保存失败'
  } finally {
    savingId.value = null
  }
}
</script>
<template>
  <PageShell class="v2-page" title="Tags" :loading="isLoading" sub-title="Gamma 同步目录 · 官方 id · 本地处置">
    <div class="filterbar">
      <el-input v-model="filters.slug" placeholder="slug 精确匹配" clearable />
      <el-select v-model="filters.seen_in_catalog" placeholder="目录来源" clearable>
        <el-option label="目录同步" value="true" />
        <el-option label="仅 event 挂载" value="false" />
      </el-select>
      <el-select v-model="filters.disposition" placeholder="处置" clearable>
        <el-option label="未标注" value="unset" />
        <el-option label="SELECT" value="SELECT" />
        <el-option label="DEFER" value="DEFER" />
        <el-option label="REJECT" value="REJECT" />
      </el-select>
      <button class="act-btn" :disabled="syncing || isLoading" @click="runSync">
        {{ syncing ? '同步中…' : '从 Gamma 同步' }}
      </button>
      <span v-if="syncMsg" class="muted">{{ syncMsg }}</span>
    </div>
    <PageState
      :loading="isLoading"
      :error="displayError" :denied="denied" :empty="!isLoading && !isError && !rows.length"
      @retry="() => refetch()">
      <el-table v-loading="isLoading" :data="rows" stripe>
        <el-table-column label="id" prop="gamma_tag_id" min-width="100" class-name="mono" />
        <el-table-column label="slug" prop="slug" min-width="150" />
        <el-table-column label="label" prop="label" min-width="150" />
        <el-table-column label="来源" width="150">
          <template #default="{ row }">
            <StatusBadge v-if="row.seen_in_catalog" tone="success">catalog</StatusBadge>
            <StatusBadge v-if="row.seen_in_event" tone="info">event</StatusBadge>
          </template>
        </el-table-column>
        <el-table-column label="events" prop="event_count" width="80" class-name="mono" />
        <el-table-column label="处置" width="150">
          <template #default="{ row }">
            <el-select
              :model-value="row.disposition ?? ''"
              :disabled="savingId === row.id"
              placeholder="未标注"
              @change="(value: TagDisposition | '') => onDisposition(row, value)"
            >
              <el-option label="未标注" value="" />
              <el-option label="SELECT" value="SELECT" />
              <el-option label="DEFER" value="DEFER" />
              <el-option label="REJECT" value="REJECT" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="observed" prop="observed_at" min-width="170" class-name="mono" />
      </el-table>
      <div class="pager">
        <span class="muted">{{ rows.length }} 条 · as_of {{ data?.as_of }}</span>
        <button class="link-btn" :disabled="!hasMore || isLoading" @click="nextPage">下一页 ›</button>
      </div>
    </PageState>
  </PageShell>
</template>
<style scoped>
.filterbar{display:flex;gap:var(--v2-space-2);margin-bottom:var(--v2-space-4);flex-wrap:wrap;align-items:center}
.filterbar .el-input,.filterbar .el-select{width:180px}
.act-btn{height:var(--v2-control-h);padding:0 14px;border:none;background:var(--v2-primary);color:#fff;cursor:pointer}
.act-btn:disabled{background:var(--v2-ink-muted);cursor:not-allowed}
.pager{display:flex;justify-content:space-between;align-items:center;margin-top:var(--v2-space-3)}
.link-btn{background:none;border:none;color:var(--v2-primary);text-decoration:underline;cursor:pointer;height:var(--v2-control-h)}
.link-btn:disabled{color:var(--v2-ink-muted);cursor:not-allowed;text-decoration:none}
.muted{color:var(--v2-ink-muted);font-size:12.5px}
:deep(.mono){font-family:var(--v2-font-mono);font-size:12px}
</style>
