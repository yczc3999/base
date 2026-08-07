<template>
  <PageShell :title="schema.title" :sub-title="schema.subTitle">
    <!-- 顶部统计卡：对 statCards.key 数值字段做当前页简单聚合 -->
    <div v-if="schema.statCards?.length" class="schema-stats">
      <div
        v-for="stat in schema.statCards"
        :key="stat.key"
        class="schema-stat"
        :style="{ '--accent': stat.accent }"
      >
        <div v-if="stat.icon" class="schema-stat-icon">
          <component :is="stat.icon" />
        </div>
        <div class="schema-stat-info">
          <div class="schema-stat-value">{{ summary[stat.key] ?? '—' }}</div>
          <div class="schema-stat-label">{{ stat.label }}</div>
        </div>
      </div>
    </div>

    <CrudTable
      ref="crudRef"
      :api="schema.api"
      :perms="schema.perms"
      :columns="schema.columns"
      :search-fields="schema.filters"
      :form-fields="schema.formFields"
      :exportable="schema.exportable"
      :importable="schema.importable"
      :import-module="schema.importModule"
      :has-create="schema.hasCreate"
      :has-edit="schema.hasEdit"
      :has-delete="schema.hasDelete"
      :action-width="schema.actionWidth"
      :show-keyword="schema.showKeyword"
      :dialog-width="schema.dialogWidth"
    >
      <!-- 工具栏：schema.batchActions 声明的批量按钮 + 页面自定义 toolbar 插槽 -->
      <template #toolbar>
        <el-button
          v-for="ba in schema.batchActions ?? []"
          :key="ba.key"
          :type="ba.danger ? 'danger' : 'default'"
          :disabled="!selectedCount"
          @click="$emit('batch-action', ba.key, crudRef?.crud?.selections?.value ?? [])"
        >
          {{ ba.label }}
        </el-button>
        <slot name="toolbar" />
      </template>
      <!-- 行操作：页面自定义 actions 插槽（未提供时回退 CrudTable 默认编辑/删除） -->
      <template v-if="hasActionsSlot" #actions="{ row }">
        <slot name="actions" :row="row" />
      </template>
      <template #form="p">
        <slot name="form" v-bind="p" />
      </template>
    </CrudTable>
  </PageShell>
</template>

<script setup lang="ts">
import { ref, computed, useSlots } from 'vue'
import PageShell from '@/components/PageShell/index.vue'
import CrudTable from '@/components/CrudTable/index.vue'
import type { CrudPageSchema } from '@/types/crudSchema'

defineOptions({ name: 'SchemaCrudPage' })

const props = defineProps<{ schema: CrudPageSchema }>()
defineEmits<{ 'batch-action': [key: string, rows: any[]] }>()

const slots = useSlots()
// 页面提供了 #actions 插槽时才接管行操作列，否则交给 CrudTable 默认（编辑/删除）
const hasActionsSlot = computed(() => Boolean(slots.actions))

const crudRef = ref()

// 简单聚合：对 statCards.key 数值字段做当前页求和（声明式统计的最小实现）
const summary = computed<Record<string, number>>(() => {
  const out: Record<string, number> = {}
  const rows: any[] = crudRef.value?.crud?.tableData?.value ?? []
  for (const stat of props.schema.statCards ?? []) {
    let sum = 0
    let has = false
    for (const row of rows) {
      const v = Number(row?.[stat.key])
      if (!Number.isNaN(v)) {
        sum += v
        has = true
      }
    }
    out[stat.key] = has ? sum : NaN
  }
  return out
})

const selectedCount = computed(() => crudRef.value?.crud?.selections?.value?.length ?? 0)

// 透出 crud 实例，页面可经 ref 访问（如调用 getList 刷新）
defineExpose({
  crud: computed(() => crudRef.value?.crud),
})
</script>

<style scoped lang="scss">
.schema-stats {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: var(--space-base);
}

.schema-stat {
  display: flex;
  align-items: center;
  gap: var(--space-md);
  padding: var(--space-base) var(--space-lg);
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-left: 3px solid var(--accent);
  border-radius: var(--radius);
}

.schema-stat-icon {
  width: 38px;
  height: 38px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: color-mix(in srgb, var(--accent) 10%, transparent);
  border-radius: var(--radius);
  color: var(--accent);
}

.schema-stat-value {
  font-size: var(--text-2xl);
  font-weight: 700;
  color: var(--text-primary);
  line-height: 1;
  font-variant-numeric: tabular-nums;
}

.schema-stat-label {
  font-size: var(--text-xs);
  color: var(--text-secondary);
  margin-top: var(--space-xs);
}
</style>
