<template>
  <PageShell title="编辑角色" sub-title="配置角色权限">
    <div class="stub-card">
      <EmptyState :icon="Shield" title="编辑角色" description="功能开发中，敬请期待" tone="primary" />
    </div>
  </PageShell>
</template>

<script setup lang="ts">
defineOptions({ name: 'RoleEdit' })
import PageShell from '@/components/PageShell/index.vue'
import EmptyState from '@/components/EmptyState/index.vue'
import { Shield } from 'lucide-vue-next'
</script>

<style scoped>
.stub-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 40px 24px;
}
</style>
