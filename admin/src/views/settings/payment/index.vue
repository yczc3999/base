<template>
  <PageShell title="支付配置" sub-title="收款渠道与结算设置">
    <div class="stub-card">
      <EmptyState :icon="CreditCard" title="支付配置" description="独立规划中..." tone="primary" />
    </div>
  </PageShell>
</template>

<script setup lang="ts">
defineOptions({ name: 'SettingsPayment' })
import PageShell from '@/components/PageShell/index.vue'
import EmptyState from '@/components/EmptyState/index.vue'
import { CreditCard } from 'lucide-vue-next'
</script>

<style scoped>
.stub-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 40px 24px;
}
</style>
