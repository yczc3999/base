<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ contentHash: string | null | undefined; label?: string }>()
const validHash = computed(() => /^[a-f0-9]{64}$/i.test(props.contentHash ?? ''))
</script>

<template>
  <RouterLink
    v-if="validHash"
    class="artifact-link"
    :to="`/v2/artifacts/${contentHash}`"
  >{{ label ?? contentHash }}</RouterLink>
  <span v-else class="muted">{{ label ?? contentHash ?? '-' }}</span>
</template>

<style scoped>
.artifact-link{color:var(--v2-primary);font-family:var(--v2-font-mono);font-size:12px;text-decoration:underline;overflow-wrap:anywhere}
.muted{color:var(--v2-ink-muted);font-family:var(--v2-font-mono);font-size:12px}
</style>
