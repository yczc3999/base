<script setup lang="ts">
/** Gamma 同步 tag 目录：查看 + 本地处置。不能手建 tag。 */
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index'
import PageShell from '@/components/PageShell/index.vue'
import { KeysetTable, useKeysetList } from '../_shared'
import { useTagsPage } from '@/queries/v2/tags'
import { saveTagDisposition, syncTagCatalog } from '@/api/v2/tags'
import type { SearchField } from '@/components/CrudTable/types'
import type { TagDisposition, TagRow } from '@/api/v2/types'
import { ApiRequestError } from '@/api/request'

const { applied, cursor, asOf, limit, page, canPrev, applyFilters, resetFilters, next, prev, setLimit, rewind } = useKeysetList()
const { data, isLoading, displayError, denied, refetch } = useTagsPage({
  filters: applied, cursor: cursor, asOf: asOf, limit: limit,
})
const rows = computed(() => data.value?.items ?? [])
function nextPage() {
  next({
    next_cursor: data.value?.next_cursor,
    as_of: data.value?.as_of,
    has_more: data.value?.has_more,
  })
}

const syncing = ref(false)
const savingId = ref<string | null>(null)

const searchFields: SearchField[] = [
  { field: 'slug', label: 'Slug', type: 'input', placeholder: '精确匹配 slug' },
  {
    field: 'seen_in_catalog', label: '来源', type: 'select',
    options: [
      { label: '目录同步', value: 'true' },
      { label: '仅 event 挂载', value: 'false' },
    ],
  },
  {
    field: 'disposition', label: '处置', type: 'select',
    options: [
      { label: '未标注', value: 'unset' },
      { label: 'SELECT', value: 'SELECT' },
      { label: 'DEFER', value: 'DEFER' },
      { label: 'REJECT', value: 'REJECT' },
    ],
  },
]

async function runSync() {
  syncing.value = true
  try {
    const result = await syncTagCatalog()
    ElMessage.success(`同步 ${result.upserted} 条 / ${result.pages} 页${result.truncated ? '（截断）' : ''}`)
    rewind()
    await refetch()
  } catch (error) {
    ElMessage.error(error instanceof ApiRequestError ? error.message : '同步失败')
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
    ElMessage.error(error instanceof ApiRequestError ? error.message : '处置保存失败')
  } finally {
    savingId.value = null
  }
}
</script>

<template>
  <PageShell class="v2-page" title="标签" sub-title="Gamma 官方目录 · 本地处置 SELECT / DEFER / REJECT">
    <KeysetTable
      :rows="rows"
      :loading="isLoading"
      :error="displayError"
      :denied="denied"
      :has-more="data?.has_more ?? false"
      :as-of="data?.as_of"
      :applied-filters="applied"
      :search-fields="searchFields"
      :page="page"
      :page-size="limit"
      :can-prev="canPrev"
      @search="applyFilters"
      @reset="resetFilters"
      @refresh="refetch"
      @retry="refetch"
      @next="nextPage"
      @prev="prev"
      @size-change="setLimit"
    >
      <template #empty>
        <p class="t">还没有标签</p>
        <p class="m">点右上角「从 Gamma 同步」。库里已同步过的会直接出现，不用再猜名字。</p>
      </template>
      <template #toolbar>
        <el-button type="primary" :loading="syncing" :disabled="isLoading" @click="runSync">
          从 Gamma 同步
        </el-button>
      </template>
      <el-table-column label="Gamma ID" prop="gamma_tag_id" min-width="110" />
      <el-table-column label="Slug" prop="slug" min-width="150" />
      <el-table-column label="名称" prop="label" min-width="150" />
      <el-table-column label="来源" width="180">
        <template #default="{ row }">
          <el-tag v-if="row.seen_in_catalog" type="success" size="small">目录</el-tag>
          <el-tag v-if="row.seen_in_event" type="info" size="small">Event</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="Events" prop="event_count" width="90" />
      <el-table-column label="处置" width="160">
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
      <el-table-column label="观测时间" prop="observed_at" min-width="170" />
    </KeysetTable>
  </PageShell>
</template>
