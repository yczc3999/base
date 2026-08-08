<template>
  <PageShell title="编辑菜单" sub-title="配置菜单项">
    <div class="stub-card">
      <EmptyState :icon="MenuSquare" title="编辑菜单" description="功能开发中，敬请期待" tone="primary" />
    </div>
  </PageShell>
</template>

<script setup lang="ts">
defineOptions({ name: 'MenuEdit' })
import PageShell from '@/components/PageShell/index.vue'
import EmptyState from '@/components/EmptyState/index.vue'
import { MenuSquare } from 'lucide-vue-next'
</script>

<style scoped>
.stub-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 40px 24px;
}
</style>
