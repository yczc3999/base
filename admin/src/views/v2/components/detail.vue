<script setup lang="ts">
/** WP-07B Component Detail：component + versions/member_contracts 链。 */
import { computed, toRef } from 'vue'
import { useRoute } from 'vue-router'
import PageShell from '@/components/PageShell/index.vue'
import { PageState, DetailSection, KeyValueGrid } from '../_shared'
import { useComponent } from '@/queries/v2/components'

const route = useRoute()
const id = toRef(route.params, 'id') as unknown as import('vue').Ref<string>
const { data, isLoading, isError, error } = useComponent(id)
const c = computed(() => data.value?.component ?? null)
</script>
<template>
  <PageShell :title="c?.component_key ?? 'Component Detail'" :loading="isLoading" sub-title="组件 · 版本 · 成员合约">
    <PageState
:loading="isLoading" :error="isError ? String(error) : null" :denied="false"
      :empty="!isLoading && !isError && !c">
      <div v-if="c">
        <DetailSection title="Component">
          <KeyValueGrid
:rows="[
            { k: 'id', v: c.id, mono: true },
            { k: 'cost_budget', v: c.cost_budget ?? '-', mono: true },
            { k: 'description', v: c.description ?? '-' },
          ]" />
        </DetailSection>
        <DetailSection title="Versions">
          <table class="mini">
            <tbody>
              <tr v-for="(v,i) in data?.versions ?? []" :key="i">
                <td class="mono">{{ v.id }}</td><td>{{ v.version_no }}</td><td>{{ v.status }}</td><td class="mono">{{ v.content_hash }}</td>
              </tr>
            </tbody>
          </table>
        </DetailSection>
        <DetailSection v-if="data?.member_contracts?.length" title="Member Contracts">
          <p class="mono">{{ JSON.stringify(data.member_contracts) }}</p>
        </DetailSection>
      </div>
    </PageState>
  </PageShell>
</template>
<style scoped>
.mini{border-collapse:collapse;width:100%}.mini td{border-bottom:1px solid var(--v2-line);padding:4px 8px;font-size:12.5px}
.mono{font-family:var(--v2-font-mono);word-break:break-all}
</style>
