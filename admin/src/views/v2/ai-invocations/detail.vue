<script setup lang="ts">
/** WP-07B AI Invocation Detail：invocation + binding/tool/validator/downstream（不内联 raw）。 */
import { computed, toRef } from 'vue'
import { useRoute } from 'vue-router'
import PageShell from '@/components/PageShell/index.vue'
import { PageState, DetailSection, KeyValueGrid } from '../_shared'
import { useAiDetail } from '@/queries/v2/ai'

const route = useRoute()
const id = toRef(route.params, 'id') as unknown as import('vue').Ref<string>
const occurredAt = toRef(route.query, 'occurred_at') as unknown as import('vue').Ref<string>
const { data, isLoading, isError, error } = useAiDetail(id, occurredAt)
const inv = computed(() => data.value?.invocation ?? null)
</script>
<template>
  <PageShell :title="inv ? `AI ${inv.id}` : 'AI Invocation Detail'" :loading="isLoading" sub-title="invocation · binding · tools · validators">
    <PageState
:loading="isLoading" :error="isError ? String(error) : null" :denied="false"
      :empty="!isLoading && !isError && !inv">
      <div v-if="inv" class="grid2">
        <DetailSection title="Invocation">
          <KeyValueGrid
:rows="[
            { k: 'id', v: inv.id, mono: true },
            { k: 'occurred_at', v: inv.occurred_at ?? '-', mono: true },
            { k: 'role', v: inv.role },
            { k: 'lifecycle', v: inv.lifecycle_state },
            { k: 'provider · model', v: `${inv.requested_provider} · ${inv.returned_model ?? inv.requested_model ?? '-'}` },
            { k: 'cost', v: inv.cost_estimated ?? '0', mono: true },
          ]" />
        </DetailSection>
        <DetailSection title="Binding / Artifact 引用">
          <p class="mono">{{ JSON.stringify(data?.model_role_binding ?? null) }}</p>
          <p class="muted">raw/prompt 内容不内联（需 v2:ai:artifact 权限按需取用）</p>
        </DetailSection>
      </div>
      <DetailSection v-if="data?.tool_calls?.length" title="Tool Calls">
        <table class="mini">
          <tbody>
            <tr v-for="(t,i) in data.tool_calls" :key="i">
              <td class="mono">{{ t.tool_type }}</td><td>{{ t.status }}</td><td class="mono">{{ t.occurred_at }}</td>
            </tr>
          </tbody>
        </table>
      </DetailSection>
      <DetailSection v-if="data?.validations?.length" title="Validators">
        <p class="mono">{{ JSON.stringify(data.validations) }}</p>
      </DetailSection>
    </PageState>
  </PageShell>
</template>
<style scoped>
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:var(--v2-space-4)}
@media (max-width:860px){.grid2{grid-template-columns:1fr}}
.mini{border-collapse:collapse;width:100%}.mini td{border-bottom:1px solid var(--v2-line);padding:4px 8px;font-size:12.5px}
.mono{font-family:var(--v2-font-mono);word-break:break-all}
.muted{color:var(--v2-ink-muted);font-size:12.5px}
</style>
