<template>
  <PageShell title="用户详情" sub-title="查看用户详细信息">
    <div class="stub-card">
      <EmptyState :icon="UserRound" title="用户详情" description="功能开发中，敬请期待" tone="primary" />
    </div>
  </PageShell>
</template>

<script setup lang="ts">
defineOptions({ name: 'UserDetail' })
import PageShell from '@/components/PageShell/index.vue'
import EmptyState from '@/components/EmptyState/index.vue'
import { UserRound } from 'lucide-vue-next'
</script>

<style scoped>
.stub-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 40px 24px;
}
</style>
