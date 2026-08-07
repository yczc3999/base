<template>
  <!-- 正常渲染子内容 -->
  <slot v-if="!hasError" />

  <!-- 子组件抛错 → 友好降级页 -->
  <div v-else class="error-boundary" role="alert" aria-live="assertive">
    <div class="eb-icon">
      <CircleAlert :size="34" :stroke-width="2" />
    </div>
    <h2 class="eb-title">页面出错了</h2>
    <p class="eb-message">{{ errorMessage }}</p>
    <el-button class="eb-retry" type="primary" :icon="RotateCcw" @click="onRetry">
      重试
    </el-button>
  </div>
</template>

<script setup lang="ts">
import { ref, onErrorCaptured } from 'vue'
import { useRouter } from 'vue-router'
import { CircleAlert, RotateCcw } from 'lucide-vue-next'

defineOptions({ name: 'AppErrorBoundary' })

const router = useRouter()
const hasError = ref(false)
const errorMessage = ref('')

/**
 * 捕获子孙组件渲染 / 生命周期中抛出的错误。
 * 返回 false 阻止错误继续冒泡（避免 Vue 全局 handler 重复告警）。
 */
onErrorCaptured((err) => {
  hasError.value = true
  errorMessage.value = err instanceof Error ? err.message : String(err)
  return false
})

/** 重试：清空错误态并整页刷新（重新走路由加载） */
function onRetry() {
  hasError.value = false
  errorMessage.value = ''
  router.go(0)
}
</script>

<style scoped lang="scss">
.error-boundary {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-md);
  min-height: 320px;
  padding: var(--space-3xl);
  text-align: center;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 4px; // 弹窗/降级浮层允许到 4px（规范上限）
}

.eb-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 64px;
  height: 64px;
  border-radius: 4px;
  background: var(--danger-bg);
  color: var(--danger);
}

.eb-title {
  margin: 0;
  font-size: var(--text-xl);
  font-weight: 700;
  color: var(--text-primary);
}

.eb-message {
  max-width: 480px;
  margin: 0;
  font-family: var(--font-mono);
  font-size: var(--text-sm);
  line-height: 1.6;
  color: var(--text-secondary);
  word-break: break-word;
}

.eb-retry {
  min-width: 96px;
}
</style>
