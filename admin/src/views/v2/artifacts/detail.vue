<script setup lang="ts">
/** Artifact drill-down is metadata-only; raw bytes remain an explicit privileged action. */
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import PageShell from '@/components/PageShell/index.vue'
import { DetailSection, KeyValueGrid, PageState } from '../_shared'
import { useArtifactMetadata } from '@/queries/v2/artifacts'

const route = useRoute()
const contentHash = computed(() => String(route.params.content_hash ?? ''))
const { data, isLoading, isError, displayError, denied, refetch } = useArtifactMetadata(contentHash, {
  enabled: computed(() => contentHash.value.length === 64),
})
const invalidHash = computed(() => contentHash.value.length !== 64)
</script>

<template>
  <PageShell class="v2-page" title="制品元数据" :loading="isLoading" sub-title="身份 · 存储 · 血缘">
    <PageState
:loading="isLoading"
      :error="invalidHash ? '无效的 artifact content hash' : (isError ? displayError : null)"
      :denied="denied"
      :empty="!invalidHash && !isLoading && !isError && !data"
      :retryable="!invalidHash"
      @retry="() => refetch()"
    >
      <template v-if="data">
        <DetailSection title="Metadata">
          <KeyValueGrid
:rows="[
            { k: 'content_hash', v: data.content_hash, mono: true },
            { k: 'content_type', v: data.content_type },
            { k: 'content_length', v: data.content_length, mono: true },
            { k: 'stored_at', v: data.stored_at ?? '-', mono: true },
          ]" />
        </DetailSection>
        <DetailSection title="Lineage">
          <table v-if="data.lineage.length" class="mini">
            <tbody>
              <tr v-for="row in data.lineage" :key="row.id">
                <td class="mono">{{ row.from_artifact_id }}</td>
                <td>{{ row.relation }}</td>
                <td class="mono">{{ row.to_artifact_id }}</td>
                <td class="mono">{{ row.created_at }}</td>
              </tr>
            </tbody>
          </table>
          <p v-else class="muted">无 lineage 记录</p>
        </DetailSection>
      </template>
    </PageState>
  </PageShell>
</template>

<style scoped>
.mini{border-collapse:collapse;width:100%}.mini td{border-bottom:1px solid var(--v2-line);padding:4px 8px;font-size:12.5px}
.mono{font-family:var(--v2-font-mono);word-break:break-all}.muted{color:var(--v2-ink-muted)}
</style>
