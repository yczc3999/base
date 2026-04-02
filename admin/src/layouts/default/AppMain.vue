<template>
  <div class="app-main">
    <router-view v-slot="{ Component }">
      <transition name="fade-slide" mode="out-in">
        <keep-alive :include="cachedViews">
          <component :is="Component" :key="$route.path" />
        </keep-alive>
      </transition>
    </router-view>
  </div>
</template>

<script setup lang="ts">
import { useTagsStore } from '@/stores/tags'
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
</style>
