<template>
  <PageShell title="编辑用户" sub-title="修改用户账号信息">
    <div class="stub-card">
      <EmptyState :icon="UserCog" title="编辑用户" description="功能开发中，敬请期待" tone="primary" />
    </div>
  </PageShell>
</template>

<script setup lang="ts">
defineOptions({ name: 'UserEdit' })
import PageShell from '@/components/PageShell/index.vue'
import EmptyState from '@/components/EmptyState/index.vue'
import { UserCog } from 'lucide-vue-next'
</script>

<style scoped>
.stub-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 40px 24px;
}
</style>
