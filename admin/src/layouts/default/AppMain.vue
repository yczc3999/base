<template>
  <main id="main-content" class="app-main">
    <router-view v-slot="{ Component }">
      <!-- 错误边界：包裹整个路由切换块（含 transition + keep-alive），
           子页面渲染/生命周期抛错时降级为友好提示页 -->
      <AppErrorBoundary>
        <transition name="fade-slide" mode="out-in">
          <keep-alive :include="cachedViews">
            <component :is="Component" />
          </keep-alive>
        </transition>
      </AppErrorBoundary>
    </router-view>
  </main>
</template>

<script setup lang="ts">
import { useTagsStore } from '@/stores/tags'
import AppErrorBoundary from '@/components/AppErrorBoundary.vue'

const tagsStore = useTagsStore()
const cachedViews = computed(() => tagsStore.cachedViews)
</script>

<style scoped lang="scss">
.app-main {
  flex: 1;
  padding: var(--space-lg);
  background: var(--bg-page);
  overflow-y: auto;
  min-height: 0;
}

@media (max-width: 600px) {
  .app-main { padding: var(--space-md); }
}
</style>
